#!/usr/bin/env python3
"""媒体探测与验证：ffprobe 元信息、字幕可用性、整段解码、视频体检。

从原 `backend/media_download_lib.py` 整段搬出，函数体逐字保留，只有跨模块引用
（`allocated_size`）改成包内 import。
"""

import json
import os
import re
import subprocess

from ._shared import allocated_size
from .paths import MIN_VIDEO_BYTES, SUBTITLE_EXTS, VIDEO_EXTS

__all__ = [
    "probe_media",
    "external_subtitle_files",
    "validate_subtitle_file",
    "decode_check",
    "verify_video",
]


def probe_media(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size:"
                "stream=index,codec_type,codec_name,width,height,channels,tags"
            ),
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffprobe failed").strip())
    return json.loads(result.stdout)


def external_subtitle_files(video_path):
    stem = os.path.splitext(video_path)[0]
    matches = []
    for ext in SUBTITLE_EXTS:
        candidate = stem + ext
        if os.path.isfile(candidate):
            matches.append(candidate)
    return matches


def validate_subtitle_file(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < 100:
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext == ".sup":
        return size >= 1024
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return False
    if ext == ".vtt":
        return bool(re.search(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->", text))
    if ext in {".srt", ".ass", ".ssa"}:
        return bool(re.search(r"\d{1,2}:\d{2}:\d{2}", text))
    return bool(text.strip())


def decode_check(path, timeout=300):
    try:
        process = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-xerror",
                "-i",
                path,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "full decode timed out", ""
    except OSError as exc:
        return False, f"ffmpeg failed to start: {exc}", ""
    stderr = (process.stderr or "").strip()
    if process.returncode != 0:
        return False, stderr[-2000:] or "ffmpeg decode failed", stderr[-2000:]
    return True, "", stderr[-2000:]


def _collect_streams(payload, result):
    """把 ffprobe 的 streams 归类计数写回 result；非法 JSON 返回 False。"""
    try:
        result["duration"] = float(payload.get("format", {}).get("duration", 0) or 0)
        streams = payload.get("streams", [])
        video_streams = [
            stream for stream in streams if stream.get("codec_type") == "video"
        ]
        audio_streams = [
            stream for stream in streams if stream.get("codec_type") == "audio"
        ]
        subtitle_streams = [
            stream for stream in streams if stream.get("codec_type") == "subtitle"
        ]
    except (TypeError, ValueError):
        return False
    result["video_streams"] = len(video_streams)
    result["audio_streams"] = len(audio_streams)
    result["subtitle_streams"] = len(subtitle_streams)
    result["embedded_subtitles"] = [
        {
            "index": stream.get("index"),
            "codec": stream.get("codec_name"),
            "tags": stream.get("tags", {}),
        }
        for stream in subtitle_streams
    ]
    return True


def verify_video(
    path,
    min_duration_seconds=60,
    deep=True,
    decode_timeout=300,
):
    """Validate allocation, streams, full decode, and subtitle availability."""
    result = {
        "path": path,
        "ok": False,
        "reason": "",
        "size": 0,
        "allocated": 0,
        "duration": 0.0,
        "video_streams": 0,
        "audio_streams": 0,
        "subtitle_streams": 0,
        "embedded_subtitles": [],
        "external_subtitles": [],
        "subtitle_ok": False,
        "decode_ok": False,
        "decode_warning": "",
    }
    if not os.path.isfile(path):
        result["reason"] = "missing"
        return result

    result["size"] = os.path.getsize(path)
    result["allocated"] = allocated_size(path)
    if result["size"] < MIN_VIDEO_BYTES:
        result["reason"] = "video is smaller than 100MB"
        return result
    if result["allocated"] < result["size"] * 0.95:
        result["reason"] = "file is sparse or incomplete"
        return result

    try:
        payload = probe_media(path)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result["reason"] = f"ffprobe failed: {exc}"
        return result

    if not _collect_streams(payload, result):
        result["reason"] = "ffprobe returned invalid JSON"
        return result

    if not result["video_streams"]:
        result["reason"] = "no video stream"
        return result
    if result["duration"] < min_duration_seconds:
        result["reason"] = f"duration is only {result['duration']:.1f}s"
        return result

    external = external_subtitle_files(path)
    result["external_subtitles"] = [
        subtitle for subtitle in external if validate_subtitle_file(subtitle)
    ]
    result["subtitle_ok"] = bool(
        result["embedded_subtitles"] or result["external_subtitles"]
    )

    if deep:
        decode_ok, decode_reason, decode_warning = decode_check(
            path,
            timeout=decode_timeout,
        )
        result["decode_ok"] = decode_ok
        result["decode_warning"] = decode_warning
        if not decode_ok:
            result["reason"] = f"ffmpeg decode failed: {decode_reason}"
            return result

    result["ok"] = True
    return result
