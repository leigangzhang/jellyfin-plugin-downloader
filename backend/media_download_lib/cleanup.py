#!/usr/bin/env python3
"""下载目录里的残留清理：专属目录识别、任务文件删除、孤儿残留审计。

从原 `backend/media_download_lib.py` 整段搬出，函数体逐字保留，只有跨模块引用
改成包内 import。

两处嵌套原来超过 4 层，把内层循环体抽成模块级私有函数：
`_is_task_artifact_name`（任务产物的名字判定）与 `_has_complete_video`
（判断一棵子树里有没有已完整落盘的视频），判定条件与顺序一字未动。
"""

import os
import shutil

from ._shared import allocated_size, path_is_within
from .paths import MIN_VIDEO_BYTES, SUBTITLE_EXTS, VIDEO_EXTS

JUNK_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".nfo",
    ".txt",
    ".url",
    ".html",
    ".htm",
    ".doc",
    ".docx",
    ".exe",
    ".js",
}

__all__ = [
    "JUNK_EXTS",
    "find_release_dirs",
    "cleanup_task_files",
    "audit_extra_files",
    "cleanup_release_junk",
]


def find_release_dirs(download_dir, info_hash, file_names=None):
    """Find only directories created for this torrent/task."""
    file_names = set(file_names or [])
    sidecars = {f".{name}.js" for name in file_names}
    hash_lower = info_hash.lower()
    matches = []
    if not os.path.isdir(download_dir):
        return matches

    for root, dirs, files in os.walk(download_dir):
        score = 0
        for name in files:
            lower = name.lower()
            if name in file_names:
                score += 1
            if name in sidecars:
                score += 4
            if lower.startswith(f".{hash_lower}.js"):
                score += 4
            if lower.startswith(".magent_") and hash_lower in lower:
                score += 4
            if lower.endswith(".torrent") and hash_lower in lower:
                score += 4
        if score and root != os.path.realpath(download_dir):
            matches.append(root)
    return sorted(set(matches), key=len)


def _is_task_artifact_name(name, file_names, sidecars, hash_lower):
    """这个文件名是不是某个迅雷任务的产物（种子、边车 js、magent 元信息）。"""
    lower = name.lower()
    return (
        name in file_names
        or name in sidecars
        or (lower.startswith(f".{hash_lower}.js"))
        or (lower.startswith(".magent_") and hash_lower in lower)
        or (lower.endswith(".torrent") and hash_lower in lower)
    )


def _remove_task_artifact(root, name, download_dir, info_hash, file_names, touched_dirs):
    """删掉一个属于本任务的产物文件；删成功则返回 (路径, 字节数)，失败返回 None。

    与原来的内联分支等价：只删 `_is_task_artifact_name` 判定为任务产物的文件，
    或落在本任务专属目录里的 `.magent_*.torrent`；OSError 一律吞掉（原样）。
    """
    full = os.path.join(root, name)
    sidecars = {f".{name}.js" for name in file_names}
    is_task_artifact = _is_task_artifact_name(
        name, file_names, sidecars, info_hash.lower()
    ) or (
        root in touched_dirs
        and name.startswith(".magent_")
        and name.lower().endswith(".torrent")
    )
    if not is_task_artifact:
        return None
    try:
        size = os.path.getsize(full)
        os.remove(full)
    except OSError:
        return None
    return full, size


def cleanup_task_files(download_dir, info_hash, file_names=None, include_videos=True):
    """Remove only files associated with one Thunder task below download_dir."""
    download_dir = os.path.realpath(download_dir)
    if not os.path.isdir(download_dir):
        return {"files": [], "bytes": 0, "dirs": []}

    file_names = set(file_names or [])
    removed_files = []
    removed_bytes = 0
    touched_dirs = set(find_release_dirs(download_dir, info_hash, file_names))

    for root, _, files in os.walk(download_dir, topdown=False):
        for name in files:
            removed = _remove_task_artifact(
                root, name, download_dir, info_hash, file_names, touched_dirs
            )
            if not removed:
                continue
            removed_files.append(removed[0])
            removed_bytes += removed[1]
            touched_dirs.add(root)

    for root in sorted(touched_dirs, key=len, reverse=True):
        if root == download_dir or not path_is_within(root, download_dir):
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass

    return {
        "files": removed_files,
        "bytes": removed_bytes,
        "dirs": sorted(touched_dirs),
    }


def _has_complete_video(root):
    """这棵子树里是否存在已完整落盘（分配量 ≈ 文件大小）的视频。"""
    for current, _, files in os.walk(root):
        for name in files:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                continue
            path = os.path.join(current, name)
            try:
                size = os.path.getsize(path)
                allocated = allocated_size(path)
            except OSError:
                continue
            if size >= MIN_VIDEO_BYTES and allocated >= size * 0.95:
                return True
    return False


def audit_extra_files(download_dir, video_paths=None):
    """Report non-media files that are not recognized Jellyfin metadata."""
    target = os.path.realpath(download_dir)
    video_paths = {os.path.realpath(path) for path in (video_paths or [])}
    metadata_ext = {".nfo", ".jpg", ".jpeg", ".png", ".webp"}
    extra = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [
            name
            for name in dirs
            if name not in {".download-candidates", "@eaDir"}
        ]
        for name in files:
            path = os.path.join(root, name)
            real = os.path.realpath(path)
            ext = os.path.splitext(name)[1].lower()
            if real in video_paths:
                continue
            if ext in VIDEO_EXTS or ext in SUBTITLE_EXTS:
                continue
            if ext in metadata_ext:
                continue
            if name in {".DS_Store", "Thumbs.db"}:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            extra.append({"path": path, "size": size})
    return extra


def _remove_quietly(path, removed):
    try:
        os.remove(path)
        removed.append(path)
    except OSError:
        pass


def cleanup_release_junk(release_dir):
    removed = []
    for root, _, files in os.walk(release_dir):
        for name in files:
            full = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            if name.startswith(".magent_") and name.endswith(".torrent"):
                _remove_quietly(full, removed)
                continue
            if name.startswith(".") and name.endswith(".js"):
                _remove_quietly(full, removed)
                continue
            if ext in VIDEO_EXTS or ext in SUBTITLE_EXTS or name.startswith("."):
                continue
            if ext in JUNK_EXTS or os.path.getsize(full) < 5 * 1024 * 1024:
                _remove_quietly(full, removed)
    return removed
