#!/usr/bin/env python3
"""Delete one Thunder task and only its local payload below a target directory."""

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
        "--force",
        action="store_true",
        help="Allow cleanup when the task appears complete",
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
            if (
                status["state"] in (3, 4)
                and status["pct"] > 99.5
                and not args.force
            ):
                print(
                    "REFUSING_TO_DELETE_COMPLETE_TASK "
                    + json.dumps(
                        {"taskid": status["taskid"], "progress": status["pct"]},
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

    deleted = 0
    removed_files = []
    removed_bytes = 0
    for info_hash, rows, file_names in targets:
        for row in rows:
            deleted += delete_task_row(row["taskid"])
        cleanup = cleanup_task_files(download_dir, info_hash, file_names)
        removed_files.extend(cleanup["files"])
        removed_bytes += cleanup["bytes"]

    subprocess.run(["open", "-a", "Thunder"], check=False)
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
