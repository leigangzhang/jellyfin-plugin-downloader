#!/usr/bin/env python3
"""下载完成后的收口：清理、校验、按命名规则搬进媒体库，并汇总警告。

从原 `backend/media_download_lib.py` 整段搬出，函数体逐字保留，只有跨模块引用
改成包内 import。

`finalize_download` 原来嵌套 6 层，把两个内层循环体抽成模块级私有函数：
`_finalize_video`（单个视频：校验 + 搬走）与 `_finalize_sidecar`（单条外挂字幕），
判定顺序、返回结构一字未动。
"""

import os

from ._shared import path_is_within
from .cleanup import audit_extra_files, cleanup_release_junk, find_release_dirs
from .db import find_task_by_hash, task_file_names
from .media import verify_video
from .paths import (
    SUBTITLE_EXTS,
    VIDEO_EXTS,
    atomic_same_volume_move,
    normalized_destination,
)

__all__ = ["finalize_download"]


def _destined(source, final_dir, media_kind, series_name, year):
    return normalized_destination(
        source,
        final_dir,
        media_kind=media_kind,
        series_name=series_name,
        year=year,
    )


def _finalize_video(source, final_dir, media_kind, series_name, year, deep_verify, min_duration_seconds):
    """校验单个视频；合格就按命名规则搬进 final_dir。

    返回 `(check, moved_path)`：check 是 verify_video 的返回结构（不合格也原样返回），
    moved_path 为真表示真的搬了一个文件。
    """
    check = verify_video(
        source,
        min_duration_seconds,
        deep=deep_verify,
    )
    if not check["ok"]:
        return check, ""
    destination = _destined(source, final_dir, media_kind, series_name, year)
    if os.path.realpath(source) == os.path.realpath(destination):
        return check, ""
    return check, atomic_same_volume_move(source, destination)


def _finalize_sidecar(source, final_dir, media_kind, series_name, year):
    """外挂字幕：落点不同才搬，已存在则不动（原样返回空串表示没搬）。"""
    destination = _destined(source, final_dir, media_kind, series_name, year)
    if os.path.realpath(source) == os.path.realpath(destination):
        return ""
    if os.path.exists(destination):
        return ""
    return atomic_same_volume_move(source, destination)


def _finalize_entry(source, entry, final_dir, media_kind, series_name, year,
                    deep_verify, min_duration_seconds):
    """处理 release 目录里的一个条目。

    返回 `(kind, payload)`：视频 -> ("video", (check, moved_path))；
    外挂字幕 -> ("subtitle", moved_path)；其它 -> ("", "")。判定顺序与原来一致。
    """
    if not os.path.isfile(source):
        return "", ""
    ext = os.path.splitext(entry)[1].lower()
    if ext in VIDEO_EXTS:
        return "video", _finalize_video(
            source,
            final_dir,
            media_kind,
            series_name,
            year,
            deep_verify,
            min_duration_seconds,
        )
    if ext in SUBTITLE_EXTS:
        return "subtitle", _finalize_sidecar(
            source, final_dir, media_kind, series_name, year
        )
    return "", ""


def _finalize_release_dir(
    release_dir, final_dir, media_kind, series_name, year,
    deep_verify, min_duration_seconds,
):
    """清理一个 release 目录并处理其中的媒体文件。

    返回 `(verified, invalid, removed_junk, moved)` 四个列表的增量，
    与原来的内层循环逐条对应。
    """
    removed_junk = cleanup_release_junk(release_dir)
    verified = []
    invalid = []
    moved = []
    for entry in os.listdir(release_dir):
        kind, payload = _finalize_entry(
            os.path.join(release_dir, entry),
            entry,
            final_dir,
            media_kind,
            series_name,
            year,
            deep_verify,
            min_duration_seconds,
        )
        if kind == "video":
            check, moved_path = payload
            if check["ok"]:
                verified.append(check)
            else:
                invalid.append(check)
            if moved_path:
                moved.append(moved_path)
        elif kind == "subtitle" and payload:
            moved.append(payload)
    return verified, invalid, removed_junk, moved


def _collect_warnings(verified, extra_files):
    warnings = []
    for item in verified:
        if not item.get("subtitle_ok"):
            warnings.append(f"no valid subtitle found for {item['path']}")
        if item.get("decode_warning"):
            warnings.append(
                f"decode warning for {item['path']}: {item['decode_warning']}"
            )
    if extra_files:
        warnings.append(f"{len(extra_files)} extra file(s) found")
    return warnings


def finalize_download(
    download_dir,
    info_hash,
    min_duration_seconds=60,
    deep_verify=False,
    final_dir=None,
    media_kind="auto",
    series_name="",
    year="",
):
    """Validate, clean, and flatten a completed download."""
    download_dir = os.path.realpath(download_dir)
    final_dir = os.path.realpath(final_dir or download_dir)
    task = None
    rows = find_task_by_hash(info_hash)
    if rows:
        task = rows[0]
    file_names = task_file_names(task) if task else []
    release_dirs = find_release_dirs(download_dir, info_hash, file_names)
    search_dirs = release_dirs or [download_dir]

    verified = []
    invalid = []
    removed_junk = []
    moved = []

    for release_dir in search_dirs:
        part = _finalize_release_dir(
            release_dir,
            final_dir,
            media_kind,
            series_name,
            year,
            deep_verify,
            min_duration_seconds,
        )
        verified.extend(part[0])
        invalid.extend(part[1])
        removed_junk.extend(part[2])
        moved.extend(part[3])

    for release_dir in sorted(release_dirs, key=len, reverse=True):
        if not path_is_within(release_dir, download_dir):
            continue
        try:
            os.rmdir(release_dir)
        except OSError:
            pass

    extra_files = audit_extra_files(final_dir)
    warnings = _collect_warnings(verified, extra_files)

    return {
        "verified": verified,
        "invalid": invalid,
        "removed_junk": removed_junk,
        "moved": moved,
        "release_dirs": release_dirs,
        "extra_files": extra_files,
        "warnings": warnings,
    }
