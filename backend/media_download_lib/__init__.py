#!/usr/bin/env python3
"""Shared helpers for Thunder download tasks and Jellyfin media finalization.

本包由原 `backend/media_download_lib.py`（单文件 39758 B / 1218 行）按主题拆出，
**行为、签名、返回结构与公共名字一律不变**：调用方全都是 `from media_download_lib
import X` 的扁平导入，所以这里把子模块的定义原对象再导出（不是包一层转发），
`import media_download_lib as mdl` 拿到的 `mdl.X` 与 `media_download_lib.<mod>.X`
是同一个对象。

为什么 `query_tasks_for_dir` / `prune_orphan_residue` 这两个函数定义在 __init__ 里、
而不是放在 cleanup.py：`tests/backend_probe/test_backend_cleanup_guard.py:78-79`
用 `mock.patch.object(mdl, "query_tasks_for_dir", ...)` / `mdl.find_release_dirs`
打桩——打桩打的是**调用点所在模块的全局名**。定义在本模块，函数体查的是本模块的
全局表，打桩才真的生效；搬进子模块后那两处 patch 会静默失效（用例仍会绿，但守卫
会退化成摆设）。这是「拆包不许削弱守卫」的具体落点。

数据根不在包内：`paths.DATA_ROOT` 取包目录的父级（= backend/）下的 state/，
与拆包前逐字相同；详见 `paths.py` 顶部注释。
"""

import json
import os
import shutil
import sqlite3

from .paths import (  # noqa: F401  -- 再导出：调用方按扁平名字 import
    DATA_ROOT,
    ETM_DB,
    GB,
    MEDIA_ROOT,
    MIN_VIDEO_BYTES,
    PAN_STATE_DIR,
    POOL_DIR,
    STAGING_ROOT,
    STATE_DIR,
    SUBTITLE_EXTS,
    VIDEO_EXTS,
    atomic_same_volume_move,
    ensure_trailing_slash,
    human_gb,
    normalized_destination,
    path_is_within,
    staging_dir_for_final,
)
from ._shared import allocated_size  # noqa: F401  -- 再导出
from .db import (  # noqa: F401  -- 再导出
    active_download_snapshot,
    cloud_task_status,
    connect_db,
    delete_task_row,
    find_task_by_hash,
    find_task_by_id,
    find_tasks_by_file_name,
    patch_download_path,
    patch_task_files,
    pause_task_row,
    task_file_names,
    task_status,
)
from .db import connect_db as _connect_db
from .torrents import (  # noqa: F401  -- 再导出
    probe_torrent_files,
    task_magent_paths,
    valid_video_files,
    write_task_magent,
)
from .cleanup import (  # noqa: F401  -- 再导出
    JUNK_EXTS,
    audit_extra_files,
    cleanup_release_junk,
    cleanup_task_files,
    find_release_dirs,
    _has_complete_video,
)
from .media import (  # noqa: F401  -- 再导出
    decode_check,
    external_subtitle_files,
    probe_media,
    validate_subtitle_file,
    verify_video,
)
from .finalize import finalize_download  # noqa: F401  -- 再导出
from .watcher import (  # noqa: F401  -- 再导出
    pid_is_running,
    read_watcher_state,
    watcher_paths,
    write_watcher_state,
)


def query_tasks_for_dir(download_dir, active_only=False):
    """迅雷 DB 里 `download_path` 指向这个目录的任务行（可只看活动任务）。"""
    target = os.path.realpath(download_dir)
    staging_target = os.path.realpath(staging_dir_for_final(target))
    conn = _connect_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT taskid, state, create_param, bt_task_info FROM etm_task")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    matches = []
    for row in rows:
        param = json.loads(row.get("create_param") or "{}")
        task_dir = os.path.realpath(param.get("download_path", ""))
        if task_dir not in {target, staging_target}:
            continue
        if active_only and int(row.get("state", 0) or 0) not in (0, 1, 2):
            continue
        matches.append(row)
    return matches


def prune_orphan_residue(download_dir, dry_run=True, protected_dirs=None):
    """Remove Thunder artifacts that **no task references**, plus empty release dirs.

    "Orphan" means no row in Thunder's task database points at it — not "the task is
    idle". A paused or failed task (state=4) is a task the user still wants, and its
    `.magent_<UUID>.torrent` is what resume needs; treating it as residue destroyed one
    download set on 2026-10-13 (see the guard below).
    """
    target = os.path.realpath(download_dir)
    if not os.path.isdir(target):
        return {"files": [], "dirs": [], "bytes": 0, "protected": []}

    protected = {
        os.path.realpath(os.path.join(target, ".download-candidates")),
    }
    protected.update(
        os.path.realpath(path) for path in (protected_dirs or [])
    )
    for row in query_tasks_for_dir(target, active_only=False):
        param = json.loads(row.get("create_param") or "{}")
        info_hash = (param.get("info_hash") or "").upper()
        names = task_file_names(row)
        if info_hash:
            protected.update(find_release_dirs(target, info_hash, names))
        # 保护范围：**任何**仍被迅雷 DB 引用的任务目录，不论 state。
        #
        # 这里原先是 `active_only=True`，它把 state 不在 (0,1,2) 的任务排除在保护之外——
        # 而迅雷的 state=4 是「失败/暂停」，正是使用者还想要、还会点「继续下载」的任务。
        # 2026-10-13 现场：三个 state=4 的任务被当成残留，其专属目录连同
        # `.magent_<UUID>.torrent` 一起被 os.remove 永久删除（不进废纸篓），
        # 之后恢复下载即报「获取种子文件失败，无法继续下载」，数据无法找回。
        # 结论：任务只要还在 DB 里，它脚下的目录就不是残留——「不活动」不等于「不要了」。
        task_name = param.get("file_name") or ""
        if task_name:
            task_dir = os.path.realpath(os.path.join(target, task_name))
            if path_is_within(task_dir, target):
                protected.add(task_dir)

    def protected_path(path):
        real = os.path.realpath(path)
        return any(path_is_within(real, root) for root in protected)

    removed_files = []
    removed_dirs = []
    removed_bytes = 0

    artifact_names = []
    for root, _, files in os.walk(target, topdown=False):
        if protected_path(root):
            continue
        for name in files:
            lower = name.lower()
            if (
                lower.startswith(".magent_")
                and lower.endswith(".torrent")
            ) or (
                name.startswith(".")
                and lower.endswith(".js")
            ) or lower.endswith(".aria2"):
                artifact_names.append(os.path.join(root, name))

    for path in artifact_names:
        if protected_path(path):
            continue
        try:
            size = os.path.getsize(path)
            if not dry_run:
                os.remove(path)
            removed_files.append(path)
            removed_bytes += size
        except OSError:
            continue

    for root, dirs, _ in os.walk(target, topdown=False):
        if root == target or protected_path(root):
            continue
        if os.path.basename(root) == ".download-candidates":
            continue

        if _has_complete_video(root):
            continue
        try:
            if not dry_run:
                shutil.rmtree(root)
            removed_dirs.append(root)
        except OSError:
            continue

    return {
        "files": removed_files,
        "dirs": removed_dirs,
        "bytes": removed_bytes,
        "protected": sorted(protected),
    }
