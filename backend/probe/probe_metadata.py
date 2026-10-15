"""metadata 抓取与打分前富化：aria2c 调用、文件清单解析、enrich。

`enrich` 从池缓存出发，只在预算允许时才走网络；`metadata_at` 的盖章规则是
`metadata_has_usable_video()`（单点定义在 probe_pool）。
"""
import os
import re
import subprocess
import time
import urllib.parse

from media_download_lib import GB, JUNK_EXTS, VIDEO_EXTS, tool_path
from probe.probe_health import ANNOUNCE_TRACKERS
from probe.probe_pool import metadata_has_usable_video


ARIA2_DHT_FLAG = "--enable-dht=true"


MIN_VIDEO_BYTES = 100 * 1024 ** 2


def parse_show_files(output):
    files = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*(\d+)\|(.+)", line)
        if not match:
            continue
        size = 0
        if index + 1 < len(lines):
            size_match = re.search(r"([\d.]+)\s*(GiB|MiB|KiB|B)", lines[index + 1])
            if size_match:
                value = float(size_match.group(1))
                unit = size_match.group(2)
                size = int(
                    value * {"GiB": GB, "MiB": 1024 ** 2, "KiB": 1024, "B": 1}[unit]
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


def fetch_metadata(info_hash, timeout, deadline=None):
    """Fetch torrent metadata through aria2c and summarize the file list.

    `deadline` is an absolute time.monotonic() value (the soft budget's end).
    The subprocess hard-kill slack is clamped to it, so a stuck aria2c cannot
    outlive the round's budget by the usual `timeout + 10` seconds.
    """
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    for tracker in ANNOUNCE_TRACKERS:
        magnet += f"&tr={urllib.parse.quote(tracker, safe='')}"

    out = os.path.join("/tmp", f"{info_hash}.torrent")
    try:
        os.remove(out)
    except FileNotFoundError:
        pass

    # 工具缺失必须在**这里**就抛出来：下面的 `except (OSError, ...)` 是给「工具在、
    # 这一轮网络/参数出问题」用的。裸名调用时 FileNotFoundError(⊂OSError) 会被它
    # 接走，于是「没装 aria2c」变成「这个种子里没有可读视频」（实测 105–228ms）。
    # ToolMissing 继承 RuntimeError，那个 except 捕获不到它。
    aria2c = tool_path("aria2c")

    hard_timeout = timeout + 10
    if deadline is not None:
        hard_timeout = max(min(hard_timeout, deadline - time.monotonic()), 1.0)

    try:
        subprocess.run(
            [
                aria2c,
                ARIA2_DHT_FLAG,
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
            timeout=hard_timeout,
            check=False,
        )
        if not os.path.exists(out):
            return None
        result = subprocess.run(
            [aria2c, "--show-files", out],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None

    name_match = re.search(r"^Name:\s+(.+)$", result.stdout, re.MULTILINE)
    total_match = re.search(r"Total Length:\s+([\d.]+)\s*([GMK])iB", result.stdout)
    if not name_match:
        return None

    total_size = 0
    if total_match:
        value = float(total_match.group(1))
        unit = total_match.group(2)
        total_size = int(value * {"G": GB, "M": 1024 ** 2, "K": 1024}[unit])

    files = parse_show_files(result.stdout)
    videos = [
        item
        for item in files
        if os.path.splitext(item["name"])[1].lower() in VIDEO_EXTS
        and item["size"] >= MIN_VIDEO_BYTES
    ]
    junk = [
        item
        for item in files
        if os.path.splitext(item["name"])[1].lower() in JUNK_EXTS
    ]
    main_video = max(videos, key=lambda item: item["size"], default=None)
    return {
        "name": name_match.group(1).strip(),
        "size_gb": total_size / GB,
        "nfiles": len(files),
        "videos": videos,
        "main_video_gb": (main_video["size"] / GB) if main_video else 0.0,
        "main_video_ext": (
            os.path.splitext(main_video["name"])[1].lower() if main_video else ""
        ),
        "junk_files": len(junk),
        "junk_bytes": sum(item["size"] for item in junk),
    }
