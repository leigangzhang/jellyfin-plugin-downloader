#!/usr/bin/env python3
"""Measure short-window torrent throughput with aria2c.

Each probe runs in an isolated temporary directory, skips file allocation, and
measures actual allocated bytes after a warm-up period. The partial payload is
deleted immediately after measurement.
"""

import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse

ANNOUNCE_TRACKERS = [
    "http://tracker.opentrackr.org:1337/announce",
    "http://explodie.org:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "http://tracker.torrent.eu.org:451/announce",
]


def allocated_bytes(root):
    total = 0
    for current, _, files in os.walk(root):
        for name in files:
            if name.endswith(".aria2"):
                continue
            path = os.path.join(current, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            total += getattr(stat, "st_blocks", 0) * 512
    return total


def measure_torrent_speed(info_hash, seconds=10, warmup=3, metadata_timeout=12):
    """Return measured payload speed and diagnostics for one info hash."""
    probe_dir = tempfile.mkdtemp(prefix=f"jellyfin-speed-{info_hash[:12]}-")
    log_path = os.path.join(probe_dir, "aria2.log")
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    for tracker in ANNOUNCE_TRACKERS:
        magnet += f"&tr={urllib.parse.quote(tracker, safe='')}"

    command = [
        "aria2c",
        "--seed-time=0",
        "--summary-interval=0",
        "--console-log-level=warn",
        "--file-allocation=none",
        "--check-integrity=false",
        "--disable-ipv6=true",
        "--bt-tracker-connect-timeout=5",
        "--bt-tracker-timeout=8",
        "--listen-port=6881-6999",
        "--dht-listen-port=6881-6999",
        f"--log={log_path}",
        "--log-level=notice",
        f"--dir={probe_dir}",
        magnet,
    ]
    result = {
        "info_hash": info_hash,
        "speed_bps": 0,
        "bytes": 0,
        "warmup_seconds": warmup,
        "measure_seconds": seconds,
        "metadata_ok": False,
        "log_tail": "",
        "error": "",
    }

    def read_log():
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    process = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 先等元数据就绪——这段不计入测速窗口，否则计时会被"等元数据"吃掉
        # （大文件/慢启动的活种会因此测出 0 字节，被误判成死种）。
        deadline = time.monotonic() + max(metadata_timeout, 1)
        while time.monotonic() < deadline:
            if "[MEMORY][METADATA]" in read_log():
                break
            if process.poll() is not None:
                break
            time.sleep(0.5)

        time.sleep(max(warmup, 0))
        if process.poll() is not None:
            text = read_log()
            result["log_tail"] = text[-2000:]
            result["metadata_ok"] = "[MEMORY][METADATA]" in text
            if not result["metadata_ok"]:
                result["error"] = f"aria2c exited early with {process.returncode}"
            return result

        before = allocated_bytes(probe_dir)
        started = time.monotonic()
        time.sleep(max(seconds, 1))
        elapsed = max(time.monotonic() - started, 0.1)
        after = allocated_bytes(probe_dir)
        downloaded = max(after - before, 0)
        result["bytes"] = downloaded
        result["speed_bps"] = downloaded / elapsed
        text = read_log()
        result["log_tail"] = text[-2000:]
        # 关键区分：拿到元数据 = 种子可达（只是窗口内没测到有效吞吐）；
        # 只有「连元数据都拿不到」才算真正不可用。
        result["metadata_ok"] = "[MEMORY][METADATA]" in text
        if downloaded == 0 and not result["metadata_ok"]:
            result["error"] = text[-2000:]
        return result
    except OSError as exc:
        result["error"] = str(exc)
        return result
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        shutil.rmtree(probe_dir, ignore_errors=True)
