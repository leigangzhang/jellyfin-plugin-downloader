#!/usr/bin/env python3
"""路径常量与落点推导：媒体根 / 暂存区 / 数据根，以及归档落点命名。

原 `backend/media_download_lib.py` 的头部常量整段搬到这里，**取值逐字不变**，
唯一必须改动的是 `DATA_ROOT` 的默认值：

    拆成包之前 `__file__` 是 `backend/media_download_lib.py`，`dirname` = `backend/`；
    拆成包之后 `__file__` 是 `backend/media_download_lib/paths.py`，`dirname` 会变成
    `backend/media_download_lib/`，数据根就会静默搬到包目录内部。而 `install.sh` 与
    `JellyfinDownloaderController.cs` 都钉着 `backend/state`，且没有测试盯着这条。
    所以这里显式取包目录的**父级**（`os.path.dirname(os.path.dirname(...))`），
    让 `DATA_ROOT` 仍等于 `<backend>/state`，`JMD_DATA_DIR` 覆盖分支同样成立。
"""

import os
import re
import subprocess

from ._shared import ensure_trailing_slash, path_is_within

__all__ = [
    "ETM_DB",
    "MEDIA_ROOT",
    "STAGING_ROOT",
    "DATA_ROOT",
    "STATE_DIR",
    "POOL_DIR",
    "PAN_STATE_DIR",
    "VIDEO_EXTS",
    "SUBTITLE_EXTS",
    "JUNK_EXTS",
    "MIN_VIDEO_BYTES",
    "GB",
    "ensure_trailing_slash",
    "staging_dir_for_final",
    "normalized_destination",
    "atomic_same_volume_move",
    "human_gb",
    "path_is_within",
]

ETM_DB = os.path.expanduser(
    "~/Library/Application Support/Thunder/etm3/etm_map.db"
)


def _default_staging_root(media_root: str) -> str:
    """同盘暂存：库根的兄弟目录 .staging，保证归档能用同盘 mv 原子完成。"""
    parent = os.path.dirname(os.path.abspath(media_root))
    if len([seg for seg in parent.split(os.sep) if seg]) >= 2:
        return os.path.join(parent, ".staging")
    return os.path.join(os.path.expanduser("~"), "Downloads", ".staging")


# 媒体库根 / 暂存区。源码里**不含任何机器相关路径**，取值顺序：
#   1. 环境变量（插件启动后端时注入）
#        JMD_MEDIA_ROOT   ← 插件配置页「媒体目录」，留空则由插件从 Jellyfin 库解析
#        JMD_STAGING_ROOT ← 插件配置页「暂存目录」，留空则由插件按同盘惯例推导
#   2. 占位默认：库根 = ~/Media；暂存 = 库根同盘的 .staging（层级太浅时 ~/Downloads/.staging）
MEDIA_ROOT = os.path.abspath(
    os.environ.get("JMD_MEDIA_ROOT")
    or os.path.join(os.path.expanduser("~"), "Media")
)
STAGING_ROOT = os.path.abspath(
    os.environ.get("JMD_STAGING_ROOT") or _default_staging_root(MEDIA_ROOT)
)
# ---------------------------------------------------------------------------
# 数据根：本插件自带后端**自己维护全部数据**（pool / watches / pan）
#
#   * 默认 = 包目录的父级（= backend/）下的 state/，随插件目录走，不碰 skill 的
#     `~/Library/Application Support/JellyfinDownloader/`（两边完全隔离）。
#   * `JMD_DATA_DIR` 可覆盖，用于迁移或临时指向别处。
#   * 数据格式以**本插件这份脚本**为准：两边格式若出现冲突，按插件写出的
#     格式走（skill 侧那份只作历史基线，不再作为对齐目标）。
# ---------------------------------------------------------------------------
DATA_ROOT = os.path.abspath(
    os.environ.get("JMD_DATA_DIR")
    or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state"
    )
)
STATE_DIR = os.path.join(DATA_ROOT, "watches")
POOL_DIR = os.path.join(DATA_ROOT, "pool")
PAN_STATE_DIR = os.path.join(DATA_ROOT, "pan")

VIDEO_EXTS = {".mkv", ".mp4", ".ts", ".avi", ".mov", ".wmv", ".m4v", ".m2ts"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".sup", ".vtt"}
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

MIN_VIDEO_BYTES = 100 * 1024 * 1024
GB = 1024**3


def staging_dir_for_final(
    final_dir,
    staging_root=STAGING_ROOT,
    media_root=MEDIA_ROOT,
):
    final_dir = os.path.realpath(final_dir)
    media_root = os.path.realpath(media_root)
    if path_is_within(final_dir, media_root):
        relative = os.path.relpath(final_dir, media_root)
    else:
        relative = os.path.basename(final_dir)
    return os.path.join(os.path.realpath(staging_root), relative)


def normalized_destination(
    source_path,
    final_dir,
    media_kind="auto",
    series_name="",
    year="",
):
    extension = os.path.splitext(source_path)[1].lower()
    basename = os.path.basename(source_path)
    marker_match = re.search(r"S(\d{2})E(\d{2,3})", basename, re.IGNORECASE)
    if media_kind == "tv" and marker_match and series_name:
        season = int(marker_match.group(1))
        episode = int(marker_match.group(2))
        filename = f"{series_name} S{season:02d}E{episode:02d}{extension}"
        return os.path.join(final_dir, f"Season {season:02d}", filename)
    if media_kind == "movie" and series_name:
        suffix = f" ({year})" if year else ""
        return os.path.join(final_dir, f"{series_name}{suffix}{extension}")
    return os.path.join(final_dir, basename)


def atomic_same_volume_move(source, destination):
    source = os.path.realpath(source)
    destination = os.path.abspath(destination)
    if os.path.exists(destination):
        raise FileExistsError(destination)
    if os.stat(os.path.dirname(source)).st_dev != os.stat(
        os.path.dirname(destination)
    ).st_dev:
        raise OSError("source and destination are not on the same volume")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    os.replace(source, destination)
    return destination


def human_gb(num_bytes):
    return num_bytes / GB
