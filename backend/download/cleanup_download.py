#!/usr/bin/env python3
"""Delete one Thunder task and only its local payload below a target directory."""

import os as _bootstrap_os
import sys as _bootstrap_sys

# 本脚本被 console_server 以 `python3 backend/<pkg>/xxx.py` 拉起时，sys.path[0] 是这个
# 子目录；包内绝对 import（from probe import ...）与根目录模块（media_download_lib /
# source_config）都要求 backend/ 在 sys.path 上，所以先把自己所在包的根补进去。
_BOOTSTRAP_ROOT = _bootstrap_os.path.dirname(
    _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
)
if _BOOTSTRAP_ROOT not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_ROOT)

import argparse
import json
import os
import subprocess
import time

from media_download_lib import (
    cleanup_task_files,
    connect_db,
    delete_task_row,
    find_task_by_hash,
    human_gb,
    prune_orphan_residue,
    staging_dir_for_final,
    task_file_names,
    task_status,
)


def close_thunder():
    subprocess.run(
        ["osascript", "-e", 'quit app "Thunder"'],
        capture_output=True,
        check=False,
    )
    time.sleep(2)
    subprocess.run(
        ["pkill", "-9", "-f", "Thunder"],
        capture_output=True,
        check=False,
    )
    time.sleep(2)


def reopen_thunder():
    subprocess.run(["open", "-a", "Thunder"], check=False)


def cycle_thunder_around_changes(enabled):
    """删除任务行前后关/开迅雷；`enabled=False` 时一个 OS 动作都不发。

    为什么需要关掉：这是真动宿主 App 的动作（quit + `pkill -9 -f Thunder` + reopen），
    离线用例只验证 DB/文件判定，不该顺带开关用户的迅雷——2026-10-14 实测
    `python3 -m unittest discover -s tests` 每跑一次就把迅雷开关一遍
    （见 `tests/test_backend_cleanup_guard.py`，普通调用方不受影响）。
    """
    if not enabled:
        return
    close_thunder()
    reopen_thunder()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("info_hash", nargs="?")
    parser.add_argument("download_dir")
    parser.add_argument(
        "--all-for-dir",
        action="store_true",
        help="Delete every Thunder task whose download path matches download_dir",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Show orphan Thunder residue without deleting it",
    )
    parser.add_argument(
        "--prune-orphans",
        action="store_true",
        help="Delete orphan Thunder residue while protecting active tasks",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-thunder-restart",
        dest="thunder_restart",
        action="store_false",
        help=(
            "Do not quit/reopen the Thunder app while cleaning "
            "(for offline callers such as tests)"
        ),
    )
    parser.add_argument(
        "--allow-task-deletion",
        "--force",
        dest="allow_task_deletion",
        action="store_true",
        help=(
            "Explicitly allow deleting a task that still exists in Thunder's database "
            "(default: refuse, whatever its state — a paused/failed task is still wanted)"
        ),
    )
    args = parser.parse_args()

    download_dir = os.path.realpath(args.download_dir)
    staging_dir = os.path.realpath(staging_dir_for_final(download_dir))
    if args.audit or args.prune_orphans:
        result = prune_orphan_residue(
            download_dir,
            dry_run=not args.prune_orphans,
        )
        print(
            ("ORPHAN_AUDIT " if args.audit else "ORPHAN_PRUNE ")
            + json.dumps(
                {
                    "files": result["files"],
                    "dirs": result["dirs"],
                    "logical_gb": human_gb(result["bytes"]),
                    "protected_dirs": result["protected"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.all_for_dir:
        conn = connect_db()
        conn.row_factory = __import__("sqlite3").Row
        cur = conn.cursor()
        cur.execute("SELECT taskid, state, create_param, bt_task_info FROM etm_task")
        all_rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        targets = []
        for row in all_rows:
            create_param = json.loads(row.get("create_param") or "{}")
            task_dir = os.path.realpath(create_param.get("download_path", ""))
            if task_dir not in {download_dir, staging_dir}:
                continue
            info_hash = (create_param.get("info_hash") or "").upper()
            if info_hash:
                targets.append((info_hash, [row], task_file_names(row)))
    else:
        if not args.info_hash:
            parser.error("info_hash is required unless --all-for-dir is used")
        info_hash = args.info_hash.upper()
        rows = find_task_by_hash(info_hash)
        targets = [(info_hash, rows, task_file_names(rows[0]) if rows else [])]

    if not targets:
        print("NO_MATCHING_TASKS; cleaning directory artifacts only")

    for info_hash, rows, _ in targets:
        task = rows[0] if rows else None
        if task:
            status = task_status(task)
            # 任何仍存在于迅雷 DB 里的任务都不再自动清理——不论 state。
            #
            # 原先的条件是 `state in (3,4) and pct > 99.5 and not force`：
            # 只有「已完成/失败**且**进度 >99.5%」才拒删，于是 state=4（失败/暂停）
            # 且进度很低的任务正好落在漏洞里被删掉——2026-10-13 三个任务即如此，
            # 其 `.magent_<UUID>.torrent` 与目录被永久删除，恢复下载报「获取种子文件失败」。
            # 新口径：任务还在 DB 里就是「还要的」，清理必须显式绕过
            # （--allow-task-deletion），否则一律拒删。
            if not args.allow_task_deletion:
                print(
                    "REFUSING_TO_DELETE_REFERENCED_TASK "
                    + json.dumps(
                        {
                            "taskid": status["taskid"],
                            "state": status["state"],
                            "progress": status["pct"],
                            "reason": (
                                "task still exists in the Thunder database; "
                                "pass --allow-task-deletion to remove it anyway"
                            ),
                        },
                        ensure_ascii=False,
                    )
                )
                return 3
            print(
                "PLAN "
                + json.dumps(
                    {
                        "taskid": status["taskid"],
                        "state": status["state"],
                        "progress": status["pct"],
                        "info_hash": info_hash,
                        "download_dir": download_dir,
                    },
                    ensure_ascii=False,
                )
            )
    if not targets:
        print("TASK_NOT_FOUND_IN_DB; cleaning matching artifacts only")

    if args.dry_run:
        return 0

    cycle_thunder_around_changes(args.thunder_restart)

    deleted = 0
    removed_files = []
    removed_bytes = 0
    for info_hash, rows, file_names in targets:
        for row in rows:
            deleted += delete_task_row(row["taskid"])
        cleanup = cleanup_task_files(download_dir, info_hash, file_names)
        removed_files.extend(cleanup["files"])
        removed_bytes += cleanup["bytes"]

    print(
        "CLEANUP_DONE "
        + json.dumps(
            {
                "tasks_deleted": deleted,
                "files_removed": len(removed_files),
                "logical_gb": human_gb(removed_bytes),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
