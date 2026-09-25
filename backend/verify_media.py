#!/usr/bin/env python3
"""Six-layer media verification for downloaded Jellyfin content."""

import argparse
import collections
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

from media_download_lib import (
    VIDEO_EXTS,
    audit_extra_files,
    probe_media,
    verify_video,
)

JELLYFIN_DB = os.path.expanduser(
    "~/Library/Application Support/jellyfin/data/jellyfin.db"
)
EPISODE_RE = re.compile(r"S(\d{1,2})E(\d{1,3})", re.IGNORECASE)
EPISODE_SHORT_RE = re.compile(r"(?:^|[^A-Z])E(\d{1,3})(?:[^0-9]|$)", re.IGNORECASE)


def stream_layer(path):
    payload = probe_media(path)
    duration = float(payload.get("format", {}).get("duration", 0) or 0)
    size = int(payload.get("format", {}).get("size", 0) or 0)
    bitrate_mbps = (size * 8 / duration / 1e6) if duration else 0.0
    streams = payload.get("streams", [])
    video = [item for item in streams if item.get("codec_type") == "video"]
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    subtitles = [item for item in streams if item.get("codec_type") == "subtitle"]
    return {
        "duration": duration,
        "size": size,
        "bitrate_mbps": bitrate_mbps,
        "bitrate_in_target_range": 4.0 <= bitrate_mbps <= 6.0,
        "video": video,
        "audio": audio,
        "subtitles": subtitles,
        "subtitle_tags": [
            {
                "index": item.get("index"),
                "codec": item.get("codec_name"),
                "language": (item.get("tags") or {}).get("language"),
                "title": (item.get("tags") or {}).get("title"),
            }
            for item in subtitles
        ],
    }


def sampled_decode_layer(path, duration, sample_seconds=20):
    samples = []
    positions = [0.05, 0.50, 0.90]
    for ratio in positions:
        start = max(duration * ratio, 0)
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    path,
                    "-t",
                    str(sample_seconds),
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=max(sample_seconds + 30, 60),
                check=False,
            )
            errors = [
                line
                for line in (result.stderr or "").splitlines()
                if line.strip()
            ]
            samples.append(
                {
                    "position": start,
                    "returncode": result.returncode,
                    "errors": errors,
                }
            )
        except subprocess.TimeoutExpired:
            samples.append(
                {
                    "position": start,
                    "returncode": -1,
                    "errors": ["decode sample timed out"],
                }
            )
    return {
        "ok": all(
            sample["returncode"] == 0 and not sample["errors"]
            for sample in samples
        ),
        "samples": samples,
    }


def hard_subtitle_layer(path, duration):
    samples = []
    positions = [0.10, 0.50, 0.90]
    for ratio in positions:
        start = max(duration * ratio, 0)
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    path,
                    "-frames:v",
                    "1",
                    "-vf",
                    "crop=iw:ih*0.12:0:ih*0.88,format=gray",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "gray",
                    "-",
                ],
                capture_output=True,
                timeout=60,
                check=False,
            )
            pixels = result.stdout
            near_white = sum(1 for value in pixels if value >= 220)
            ratio_white = near_white / len(pixels) if pixels else 0.0
            samples.append(
                {
                    "position": start,
                    "pixels": len(pixels),
                    "near_white": near_white,
                    "ratio": ratio_white,
                }
            )
        except subprocess.TimeoutExpired:
            samples.append(
                {
                    "position": start,
                    "pixels": 0,
                    "near_white": 0,
                    "ratio": 0.0,
                    "error": "hard subtitle sample timed out",
                }
            )
    max_ratio = max((item["ratio"] for item in samples), default=0.0)
    return {
        "suspected": 0.002 <= max_ratio <= 0.20,
        "max_white_ratio": max_ratio,
        "samples": samples,
    }


def episode_number(name):
    match = EPISODE_RE.search(name)
    if match:
        return int(match.group(2))
    match = EPISODE_SHORT_RE.search(name)
    return int(match.group(1)) if match else None


def consistency_layer(directory):
    videos = []
    metadata_ext = {".nfo", ".jpg", ".jpeg", ".png", ".webp"}
    for root, _, files in os.walk(directory):
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                path = os.path.join(root, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                if size >= 100 * 1024 * 1024:
                    videos.append(path)

    episodes = collections.defaultdict(list)
    small_files = []
    for root, _, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if (
                0 <= size < 100 * 1024
                and os.path.splitext(name)[1].lower() not in metadata_ext
            ):
                small_files.append({"path": path, "size": size})
            number = episode_number(name)
            if number is not None and path in videos:
                episodes[number].append(path)

    present = sorted(episodes)
    missing = []
    if present:
        missing = sorted(set(range(present[0], present[-1] + 1)) - set(present))

    duplicate_episodes = {
        str(number): paths
        for number, paths in episodes.items()
        if len(paths) > 1
    }

    duplicate_large = []
    signatures = collections.defaultdict(list)
    for path in videos:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        signature = (os.path.basename(path).lower(), stat.st_size)
        signatures[signature].append(path)
    for (name, size), paths in signatures.items():
        if len(paths) > 1:
            duplicate_large.append(
                {"name": name, "size": size, "paths": paths}
            )

    missing_metadata = []
    filenames = [
        str(Path(root) / name)
        for root, _, files in os.walk(directory)
        for name in files
    ]
    for number, paths in episodes.items():
        marker = f"E{number:02d}".lower()
        related = [path for path in filenames if marker in os.path.basename(path).lower()]
        has_nfo = any(path.lower().endswith(".nfo") for path in related)
        has_thumb = any(
            path.lower().endswith((".jpg", ".jpeg", ".png"))
            for path in related
        )
        if not has_nfo or not has_thumb:
            missing_metadata.append(
                {
                    "episode": number,
                    "missing_nfo": not has_nfo,
                    "missing_thumb": not has_thumb,
                    "video": paths[0],
                }
            )

    return {
        "video_count": len(videos),
        "episodes": present,
        "missing_episodes": missing,
        "duplicate_episodes": duplicate_episodes,
        "duplicate_large_files": duplicate_large,
        "small_files": small_files,
        "episodes_missing_metadata": missing_metadata,
    }


def jellyfin_db_layer(directory):
    if not os.path.isfile(JELLYFIN_DB):
        return {"available": False, "error": "Jellyfin DB not found"}
    uri = f"file:{JELLYFIN_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT Id, Name, Type, Path, SeriesName, IndexNumber, ParentIndexNumber
        FROM BaseItems
        WHERE Path LIKE ?
        """,
        (directory.rstrip(os.sep) + os.sep + "%",),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    ghost_entries = []
    db_video_paths = set()
    for row in rows:
        path = row.get("Path")
        if not path:
            continue
        if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
            db_video_paths.add(os.path.realpath(path))
        if not os.path.exists(path):
            ghost_entries.append(row)

    disk_video_paths = set()
    for root, _, files in os.walk(directory):
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                disk_video_paths.add(os.path.realpath(os.path.join(root, name)))

    duplicate_entries = []
    groups = collections.defaultdict(list)
    for row in rows:
        if row.get("Type", "").endswith("Episode"):
            groups[
                (
                    row.get("SeriesName"),
                    row.get("ParentIndexNumber"),
                    row.get("IndexNumber"),
                )
            ].append(row)
    for key, items in groups.items():
        if len(items) > 1:
            duplicate_entries.append(
                {"series": key[0], "season": key[1], "episode": key[2], "items": items}
            )

    return {
        "available": True,
        "entries": rows,
        "ghost_entries": ghost_entries,
        "duplicate_entries": duplicate_entries,
        "unindexed_video_files": sorted(disk_video_paths - db_video_paths),
        "indexed_missing_video_files": sorted(db_video_paths - disk_video_paths),
    }


def structure_layer(directory):
    extras = audit_extra_files(directory)
    artifacts = []
    hygiene_dirs = []
    orphan_release_dirs = []
    for root, dirs, files in os.walk(directory):
        if root != directory and ".download-candidates" not in Path(root).parts:
            has_large_video = False
            for current, _, nested_files in os.walk(root):
                if any(
                    os.path.splitext(name)[1].lower() in VIDEO_EXTS
                    and os.path.getsize(os.path.join(current, name)) >= 100 * 1024 * 1024
                    for name in nested_files
                ):
                    has_large_video = True
                    break
            if not has_large_video and not any(
                os.path.splitext(name)[1].lower() in VIDEO_EXTS for name in files
            ):
                orphan_release_dirs.append(root)
        for name in dirs:
            if name in {".download-candidates", "@eaDir"}:
                hygiene_dirs.append(os.path.join(root, name))
        for name in files:
            lower = name.lower()
            if (
                lower.startswith(".magent_")
                or (name.startswith(".") and lower.endswith(".js"))
                or lower.endswith((".aria2", ".magnets"))
                or name in {".DS_Store", "Thumbs.db"}
            ):
                artifacts.append(os.path.join(root, name))
    return {
        "extra_files": extras,
        "thunder_artifacts": artifacts,
        "hygiene_dirs": hygiene_dirs,
        "orphan_release_dirs": sorted(set(orphan_release_dirs)),
    }


def verify_file(path, include_hard_subtitles=True):
    base = verify_video(path, deep=False)
    if not base["ok"]:
        return {
            "path": path,
            "ok": False,
            "stream": {},
            "samples": {},
            "hard_subtitles": {},
            "base": base,
        }
    streams = stream_layer(path)
    samples = sampled_decode_layer(path, streams["duration"])
    hard_subtitles = (
        hard_subtitle_layer(path, streams["duration"])
        if include_hard_subtitles
        else {}
    )
    return {
        "path": path,
        "ok": samples["ok"],
        "stream": streams,
        "samples": samples,
        "hard_subtitles": hard_subtitles,
        "base": base,
    }


def verify_directory(directory, include_hard_subtitles=True):
    directory = os.path.realpath(directory)
    files = []
    for root, _, names in os.walk(directory):
        for name in names:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                files.append(os.path.join(root, name))
    file_results = [
        verify_file(path, include_hard_subtitles=include_hard_subtitles)
        for path in sorted(files)
    ]
    consistency = consistency_layer(directory)
    database = jellyfin_db_layer(directory)
    structure = structure_layer(directory)
    warnings = []
    for item in file_results:
        if not item.get("base", {}).get("subtitle_ok"):
            warnings.append(f"no subtitle found: {item['path']}")
        bitrate = item.get("stream", {}).get("bitrate_mbps")
        if bitrate and not item["stream"].get("bitrate_in_target_range"):
            warnings.append(
                f"bitrate outside preferred 4-6 Mbps: {bitrate:.2f} Mbps "
                f"for {item['path']}"
            )
    warnings.extend(
        f"missing episode E{number:02d}"
        for number in consistency["missing_episodes"]
    )
    warnings.extend(
        f"duplicate episode {number}"
        for number in consistency["duplicate_episodes"]
    )
    warnings.extend(
        f"ghost Jellyfin path: {item.get('Path')}"
        for item in database.get("ghost_entries", [])
    )
    warnings.extend(
        f"orphan release directory: {path}"
        for path in structure.get("orphan_release_dirs", [])
    )
    return {
        "directory": directory,
        "ok": all(item["ok"] for item in file_results),
        "warnings": warnings,
        "files": file_results,
        "consistency": consistency,
        "database": database,
        "structure": structure,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--json-out")
    parser.add_argument("--skip-hard-subtitles", action="store_true")
    args = parser.parse_args()
    result = verify_directory(
        args.directory,
        include_hard_subtitles=not args.skip_hard_subtitles,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    return 0 if result["ok"] else 6


if __name__ == "__main__":
    raise SystemExit(main())
