#!/usr/bin/env python3
"""种子元信息：抓取 metainfo、任务目录里的 `.magent_*.torrent`、文件筛选。

从原 `backend/media_download_lib.py` 整段搬出，函数体逐字保留；`allocated_size`
从 `_shared` 再导出，保持「公共名一个不少」。
"""

import hashlib
import os
import re
import subprocess
import uuid
import shutil

from ._shared import allocated_size, tool_path  # noqa: F401  -- 再导出：allocated_size 是公共名
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

    # 缺 aria2c 必须抛 ToolMissing，不能返回 []：调用方（submit 的
    # `probe_torrent_files` → `if not videos: RESOURCE_REJECTED`）会把空列表
    # 解读成「这个种子里没有可读视频」并 rc=3 —— 环境故障伪装成资源问题。
    aria2c = tool_path("aria2c")

    try:
        subprocess.run(
            [
                aria2c,
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
            [aria2c, "--show-files", out],
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


# bencode 嵌套上限：只为防畸形文件把解析拖进死循环，正常种子远达不到。
_BENCODE_MAX_DEPTH = 32


def _read_bencode_bytes(blob, index):
    """读一个 bencode 字符串，返回 `(内容, 结束下标)`；结构不对抛 ValueError。"""
    colon = blob.find(b":", index)
    if colon < 0:
        raise ValueError("bencode 字符串缺少长度分隔符")
    digits = blob[index:colon]
    if not digits.isdigit():
        raise ValueError("bencode 字符串长度不是数字")
    start = colon + 1
    end = start + int(digits)
    if end > len(blob):
        raise ValueError("bencode 字符串超出文件末尾")
    return blob[start:end], end


def _skip_bencode_value(blob, index, depth=0):
    """跳过 `blob[index:]` 处的一个 bencode 值，返回其后的下标。"""
    if depth > _BENCODE_MAX_DEPTH:
        raise ValueError("bencode 嵌套过深")
    head = blob[index : index + 1]
    if head == b"i":
        end = blob.find(b"e", index)
        if end < 0:
            raise ValueError("bencode 整数没有结束符")
        return end + 1
    if head in (b"l", b"d"):
        index += 1
        while True:
            if index >= len(blob):
                raise ValueError("bencode 容器没有结束符")
            if blob[index : index + 1] == b"e":
                return index + 1
            if head == b"d":
                _, index = _read_bencode_bytes(blob, index)
            index = _skip_bencode_value(blob, index, depth + 1)
    if not head:
        raise ValueError("bencode 值在文件末尾截断")
    _, end = _read_bencode_bytes(blob, index)
    return end


def _raw_info_bytes(blob):
    """取出顶层字典里 `info` 键对应的**原始字节**；不是合法种子返回 None。

    必须取原样字节：bencode 重编码不保证逐字节相同，重编码后算出的 info_hash 会漂。
    """
    if blob[:1] != b"d":
        return None
    index = 1
    try:
        while index < len(blob) and blob[index : index + 1] != b"e":
            key, index = _read_bencode_bytes(blob, index)
            start = index
            index = _skip_bencode_value(blob, index)
            if key == b"info":
                return blob[start:index]
    except (ValueError, IndexError):
        return None
    return None


def _torrent_info_hash(path):
    """从种子文件算出 info_hash（sha1 of `info` 段原始字节）；算不出返回 None。"""
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError:
        return None
    info = _raw_info_bytes(blob)
    if info is None:
        return None
    return hashlib.sha1(info).hexdigest().upper()


def _magent_for_info_hash(task_dir, info_hash):
    """本任务（按 info_hash 认领）已在场的 metainfo 路径；没有返回 None。

    两步认领：先看文件名里有没有 info_hash（我们自己写的那份就带），再解析
    `.magent_*.torrent` 的 bencode 取 info 段算 sha1 —— 迅雷自己写的是
    `.magent_<UUID>.torrent`，名字里没有 hash，只能靠内容认。
    """
    for path in task_magent_paths(task_dir):
        try:
            if os.path.getsize(path) <= 0:
                continue
        except OSError:
            continue
        if info_hash in os.path.basename(path).upper():
            return path
        if _torrent_info_hash(path) == info_hash:
            return path
    return None


def write_task_magent(task_dir, info_hash, source=None):
    """把 metainfo 写进任务目录，供迅雷在恢复时读取——**不等迅雷自己写**。

    为什么必须由我们写：迅雷通过 `open magnet:` 建任务时负责把 metadata 落成
    `.magent_<UUID>.torrent`；实测它有时不落盘（2026-10-13：任务 287/289 的目录是空的），
    之后点「继续下载」就报「获取种子文件失败，无法继续下载」。既然
    `probe_torrent_files()` 早就把同一份 metainfo 抓到了 `/tmp/<hash>.torrent`，
    就没有理由让恢复能力依赖迅雷那个不确定的落盘动作。

    **短路按 info_hash，不按目录**：同一暂存目录里可以坐着多个任务（2026-10-15：
    297「国英多音轨」与 299「中文字幕」同在 `一战再战 (2025)/`）。按目录判定时第二条
    看到「目录里已有非空 .torrent」就跳过，自己的 metainfo 从未落盘 —— 它一暂停再恢复
    就必然报上面那个错。只有「本任务那份已在场」才算数。

    返回 ``{"path", "written", "existed", "bytes"}``；找不到 metainfo 时返回
    ``{"path": "", "written": False, "error": ...}``。
    """
    task_dir = os.path.realpath(task_dir)
    wanted = (info_hash or "").strip().upper()
    source = source or f"/tmp/{wanted}.torrent"

    if wanted:
        existing = _magent_for_info_hash(task_dir, wanted)
    else:
        # 拿不到 info_hash 就没法归属（正常调用不会发生）：退回按目录判定，
        # 保持这条边界的旧行为，而不是每次都新写一份。
        candidates = [
            path for path in task_magent_paths(task_dir) if os.path.getsize(path) > 0
        ]
        existing = candidates[0] if candidates else None

    if existing:
        return {
            "path": existing,
            "written": False,
            "existed": True,
            "bytes": os.path.getsize(existing),
        }
    if not os.path.isfile(source) or os.path.getsize(source) <= 0:
        return {
            "path": "",
            "written": False,
            "existed": False,
            "error": f"没有可用的 metainfo：{source}",
        }
    os.makedirs(task_dir, exist_ok=True)
    # 文件名带 info_hash：人工排查时一眼看出这份 metainfo 属于哪个任务；
    # 名字确定了，重复写同一 info_hash 也就天然幂等（上面已短路，不会覆盖）。
    name = (
        f".magent_{wanted}.torrent"
        if wanted
        else f".magent_{uuid.uuid4().hex.upper()}.torrent"
    )
    target = os.path.join(task_dir, name)
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
