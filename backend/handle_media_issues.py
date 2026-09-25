#!/usr/bin/env python3
"""Apply safe remediation actions for six-layer verification findings."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import urllib.error
import urllib.request

from media_download_lib import (
    JUNK_EXTS,
    prune_orphan_residue,
)
from verify_media import verify_directory

JELLYFIN_DB = os.path.expanduser(
    "~/Library/Application Support/jellyfin/data/jellyfin.db"
)
JELLYFIN_URL = "http://127.0.0.1:8096"


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def preferred_path(paths):
    return min(
        paths,
        key=lambda path: (
            path.count(os.sep),
            len(path),
            path.lower(),
        ),
    )


def dedupe_verified_files(consistency, apply):
    removed = []
    kept = []
    for group in consistency.get("duplicate_large_files", []):
        hashes = {}
        for path in group["paths"]:
            try:
                digest = file_sha256(path)
            except OSError:
                continue
            hashes.setdefault(digest, []).append(path)
        for digest, paths in hashes.items():
            if len(paths) < 2:
                continue
            keeper = preferred_path(paths)
            kept.append({"sha256": digest, "path": keeper})
            for path in paths:
                if path == keeper:
                    continue
                if apply:
                    try:
                        os.remove(path)
                    except OSError:
                        continue
                removed.append(path)
    return {"removed": removed, "kept": kept}


def remove_suspicious_small_files(consistency, apply):
    removed = []
    for item in consistency.get("small_files", []):
        path = item["path"]
        ext = os.path.splitext(path)[1].lower()
        if item["size"] == 0 or ext in JUNK_EXTS:
            if apply:
                try:
                    os.remove(path)
                except OSError:
                    continue
            removed.append(path)
    return removed


def db_referenced_paths(database):
    return {
        os.path.realpath(item["Path"])
        for item in database.get("entries", [])
        if item.get("Path")
    }


def handle_orphan_release_dirs(structure, database, apply):
    referenced = db_referenced_paths(database)
    removed = []
    deferred = []
    for directory in structure.get("orphan_release_dirs", []):
        real = os.path.realpath(directory)
        if any(
            path == real or path.startswith(real + os.sep)
            for path in referenced
        ):
            deferred.append(
                {
                    "action": "refresh_jellyfin_before_removal",
                    "path": directory,
                }
            )
            continue
        if apply:
            try:
                shutil.rmtree(directory)
            except OSError:
                continue
        removed.append(directory)
    return {"removed": removed, "deferred": deferred}


def jellyfin_access_token():
    if not os.path.isfile(JELLYFIN_DB):
        return ""
    conn = sqlite3.connect(
        f"file:{JELLYFIN_DB}?mode=ro",
        uri=True,
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT AccessToken
        FROM Devices
        WHERE AccessToken IS NOT NULL AND AccessToken != ''
        ORDER BY DateLastActivity DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else ""


def trigger_jellyfin_refresh():
    token = jellyfin_access_token()
    if not token:
        return {
            "ok": False,
            "action": "manual_jellyfin_refresh",
            "reason": "no stored Jellyfin access token",
        }
    request = urllib.request.Request(
        f"{JELLYFIN_URL}/Library/Refresh",
        method="POST",
        headers={
            "Authorization": f'MediaBrowser Token="{token}"',
            "X-Emby-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return {"ok": 200 <= response.status < 300, "status": response.status}
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {
            "ok": False,
            "action": "manual_jellyfin_refresh",
            "reason": str(exc),
        }


def handle_issues(directory, verification=None, apply=True):
    verification = verification or verify_directory(directory)
    consistency = verification["consistency"]
    database = verification["database"]
    structure = verification["structure"]

    orphan_release_dirs = handle_orphan_release_dirs(
        structure,
        database,
        apply,
    )
    protected_dirs = [
        item["path"] for item in orphan_release_dirs["deferred"]
    ]
    actions = {
        "duplicate_files": dedupe_verified_files(consistency, apply),
        "small_files_removed": remove_suspicious_small_files(
            consistency,
            apply,
        ),
        "orphan_release_dirs": orphan_release_dirs,
        "orphan_residue": prune_orphan_residue(
            directory,
            dry_run=not apply,
            protected_dirs=protected_dirs,
        ),
        "pending_actions": [],
        "manual_actions": [],
    }

    if (
        database.get("ghost_entries")
        or database.get("duplicate_entries")
        or database.get("indexed_missing_video_files")
        or database.get("unindexed_video_files")
    ):
        if apply:
            actions["jellyfin_refresh"] = trigger_jellyfin_refresh()
        else:
            actions["jellyfin_refresh"] = {
                "ok": False,
                "action": "would_refresh_jellyfin",
            }

    if consistency.get("missing_episodes"):
        actions["pending_actions"].append(
            {
                "action": "search_missing_episodes",
                "episodes": consistency["missing_episodes"],
            }
        )

    missing_subtitles = [
        item["path"]
        for item in verification["files"]
        if not item.get("base", {}).get("subtitle_ok")
    ]
    if missing_subtitles:
        actions["pending_actions"].append(
            {
                "action": "search_subtitles",
                "files": missing_subtitles,
            }
        )

    bitrate_issues = []
    for item in verification["files"]:
        stream = item.get("stream", {})
        video = stream.get("video") or []
        width = video[0].get("width") if video else None
        height = video[0].get("height") if video else None
        bitrate = stream.get("bitrate_mbps")
        if (
            width
            and height
            and (width, height) != (1920, 1080)
        ) or (
            bitrate
            and not stream.get("bitrate_in_target_range")
        ):
            bitrate_issues.append(
                {
                    "path": item["path"],
                    "width": width,
                    "height": height,
                    "bitrate_mbps": bitrate,
                    "preferred": "1920x1080, 4-6 Mbps",
                }
            )
    if bitrate_issues:
        actions["pending_actions"].append(
            {
                "action": "reselect_bitrate_compliant_resource",
                "files": bitrate_issues,
            }
        )

    for item in verification["files"]:
        if item.get("hard_subtitles", {}).get("suspected"):
            actions["manual_actions"].append(
                {
                    "action": "review_hard_subtitles",
                    "path": item["path"],
                    "max_white_ratio": item["hard_subtitles"][
                        "max_white_ratio"
                    ],
                }
            )

    return actions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--verify-json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.verify_json:
        with open(args.verify_json, "r", encoding="utf-8") as handle:
            verification = json.load(handle)
    else:
        verification = verify_directory(args.directory)
    actions = handle_issues(
        args.directory,
        verification=verification,
        apply=args.apply,
    )
    print(json.dumps(actions, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
