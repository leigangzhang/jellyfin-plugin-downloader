#!/usr/bin/env python3
"""种子元信息：抓取 metainfo、任务目录里的 `.magent_*.torrent`、文件筛选。

从原 `backend/media_download_lib.py` 整段搬出，函数体逐字保留；`allocated_size`
从 `_shared` 再导出，保持「公共名一个不少」。
"""

import os
import re
import subprocess
import uuid
import shutil

from ._shared import allocated_size  # noqa: F401  -- 再导出：allocated_size 是公共名
from .paths import GB, MIN_VIDEO_BYTES, VIDEO_EXTS

__all__ = [
    "probe_torrent_files",
    "task_magent_paths",
    "write_task_magent",
    "valid_video_files",
    "allocated_size",
]


def probe_torrent_files(info_hash, timeout=25):
    """Fetch torrent metadata and return a list of file dictionaries."""
    import urllib.parse

    trackers = [
        "http://tracker.opentrackr.org:1337/announce",
        "http://tracker.openbittorrent.com:80/announce",
        "http://explodie.org:6969/announce",
    ]
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    for tracker in trackers:
        magnet += f"&tr={urllib.parse.quote(tracker, safe='')}"
    out = f"/tmp/{info_hash}.torrent"
    try:
        os.remove(out)
    except FileNotFoundError:
        pass

    try:
        subprocess.run(
            [
                "aria2c",
                "--bt-metadata-only=true",
                "--bt-save-metadata=true",
                "--seed-time=0",
                f"--bt-stop-timeout={timeout}",
                "--summary-interval=0",
                "--disable-ipv6=true",
                "--bt-tracker-connect-timeout=5",
                "--bt-tracker-timeout=8",
                "--dir=/tmp",
                magnet,
            ],
            capture_output=True,
            timeout=timeout + 10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return []

    if not os.path.exists(out):
        return []

    try:
        result = subprocess.run(
            ["aria2c", "--show-files", out],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return []

    files = []
    lines = result.stdout.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*(\d+)\|(.+)", line)
        if not match:
            continue
        size = 0
        if index + 1 < len(lines):
            size_match = re.search(
                r"([\d.]+)\s*(GiB|MiB|KiB|B)", lines[index + 1]
            )
            if size_match:
                value = float(size_match.group(1))
                unit = size_match.group(2)
                size = int(
                    value
                    * {
                        "GiB": GB,
                        "MiB": 1024**2,
                        "KiB": 1024,
                        "B": 1,
                    }[unit]
                )
        path = match.group(2).strip()
        files.append(
            {
                "index": int(match.group(1)) - 1,
                "path": path,
                "name": os.path.basename(path),
                "size": size,
            }
        )
    return files


def task_magent_paths(task_dir):
    """任务目录里现存的 `.magent_*.torrent` 列表（迅雷自己放的那个）。"""
    if not os.path.isdir(task_dir):
        return []
    return sorted(
        os.path.join(task_dir, name)
        for name in os.listdir(task_dir)
        if name.startswith(".magent_") and name.endswith(".torrent")
    )


def write_task_magent(task_dir, info_hash, source=None):
    """把 metainfo 写进任务目录，供迅雷在恢复时读取——**不等迅雷自己写**。

    为什么必须由我们写：迅雷通过 `open magnet:` 建任务时负责把 metadata 落成
    `.magent_<UUID>.torrent`；实测它有时不落盘（2026-10-13：任务 287/289 的目录是空的），
    之后点「继续下载」就报「获取种子文件失败，无法继续下载」。既然
    `probe_torrent_files()` 早就把同一份 metainfo 抓到了 `/tmp/<hash>.torrent`，
    就没有理由让恢复能力依赖迅雷那个不确定的落盘动作。

    返回 ``{"path", "written", "existed", "bytes"}``；找不到 metainfo 时返回
    ``{"path": "", "written": False, "error": ...}``。
    """
    task_dir = os.path.realpath(task_dir)
    source = source or f"/tmp/{info_hash.upper()}.torrent"
    existing = [p for p in task_magent_paths(task_dir) if os.path.getsize(p) > 0]
    if existing:
        return {
            "path": existing[0],
            "written": False,
            "existed": True,
            "bytes": os.path.getsize(existing[0]),
        }
    if not os.path.isfile(source) or os.path.getsize(source) <= 0:
        return {
            "path": "",
            "written": False,
            "existed": False,
            "error": f"没有可用的 metainfo：{source}",
        }
    os.makedirs(task_dir, exist_ok=True)
    target = os.path.join(
        task_dir, f".magent_{uuid.uuid4().hex.upper()}.torrent"
    )
    shutil.copyfile(source, target)
    size = os.path.getsize(target)
    if size <= 0:
        return {
            "path": target,
            "written": False,
            "existed": False,
            "error": "写入后文件为空",
        }
    return {"path": target, "written": True, "existed": False, "bytes": size}


def valid_video_files(files, min_bytes=MIN_VIDEO_BYTES):
    videos = []
    for item in files:
        ext = os.path.splitext(item["name"])[1].lower()
        if ext in VIDEO_EXTS and item["size"] >= min_bytes:
            videos.append(item)
    return sorted(videos, key=lambda item: item["path"])
