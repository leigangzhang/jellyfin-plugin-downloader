#!/usr/bin/env python3
"""Check if a media item already exists in a Jellyfin category directory.

Does fuzzy matching on directory names (ignoring case, spaces, punctuation, year).
When a directory matches, checks for real video files (>100MB) inside.

Exit codes / output:
  ALREADY_EXISTS   — directory has real video files, skip download
  NEEDS_DOWNLOAD   — directory exists but has no real video (placeholder/empty), proceed
  NOT_FOUND        — no matching directory, proceed
  CATEGORY_DIR_MISSING / INVALID_QUERY — error

Usage:
    python3 check_exists.py "热辣滚烫" Movies
    python3 check_exists.py "Dark Glory S2" "TV Shows"
"""
import argparse
import os
import re
import sys

# 落点根路径只有一处定义（media_download_lib，支持 JMD_MEDIA_ROOT 覆盖），
# 这里不再各写一份，避免两处不同步导致「查重查的是 A、下载落到 B」。
from media_download_lib import MEDIA_ROOT  # noqa: E402

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".wmv",
              ".flv", ".mov", ".rmvb", ".rm", ".m4v", ".mpg", ".mpeg"}
MIN_VALID_BYTES = 100 * 1024 * 1024  # 100 MB

CLEAN_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]")

def normalize(name):
    """Lowercase, strip year, remove non-alphanumeric/CJK."""
    n = name.lower()
    n = re.sub(r"\((19|20)\d{2}\)", "", n)
    n = re.sub(r"(19|20)\d{2}", "", n)
    n = CLEAN_RE.sub("", n)
    return n

def allocated_size(path):
    """Return bytes actually allocated on disk.

    Thunder creates sparse files whose logical size can look complete while
    almost no payload has been written. Jellyfin checks must never treat those
    as existing media.
    """
    stat = os.stat(path)
    return getattr(stat, "st_blocks", 0) * 512


def find_valid_video(root):
    """Return list of (path, size_mb) for allocated video files > 100MB."""
    found = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            ext = os.path.splitext(f)[1].lower()
            if ext in VIDEO_EXTS:
                p = os.path.join(dp, f)
                try:
                    sz = os.path.getsize(p)
                    allocated = allocated_size(p)
                except OSError:
                    continue
                # Completed files should have nearly all payload allocated.
                # Sparse/preallocated partial files are not real media.
                if sz >= MIN_VALID_BYTES and allocated >= sz * 0.95:
                    found.append((p, sz / 1024**2))
    return found

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="Movie/show title to check")
    ap.add_argument("category", help="Movies / TV Shows / Shows / Records")
    args = ap.parse_args()

    target_dir = os.path.join(MEDIA_ROOT, args.category)
    if not os.path.isdir(target_dir):
        print(f"CATEGORY_DIR_MISSING: {target_dir}")
        sys.exit(1)

    query_norm = normalize(args.name)
    if not query_norm:
        print("INVALID_QUERY")
        sys.exit(1)

    matches = []
    for entry in os.listdir(target_dir):
        full = os.path.join(target_dir, entry)
        if not os.path.isdir(full):
            continue
        entry_norm = normalize(entry)
        if len(query_norm) >= 3 and (query_norm in entry_norm or entry_norm in query_norm) or len(query_norm) >= 4 and len(entry_norm) >= 4 and query_norm[:4] == entry_norm[:4]:
            matches.append(entry)

    if not matches:
        print(f"NOT_FOUND in {args.category}")
        return

    # Evaluate each match: does it contain a real video?
    any_real = False
    print(f"DIR_MATCH in {args.category}:")
    for m in matches:
        full = os.path.join(target_dir, m)
        videos = find_valid_video(full)
        if videos:
            any_real = True
            total_gb = sum(sz for _, sz in videos) / 1024
            print(f"  [HAS_VIDEO] {m}  ({len(videos)} file(s), {total_gb:.1f} GB)")
            for vp, sz in videos[:3]:
                print(f"             - {os.path.basename(vp)} ({sz:.0f} MB)")
            if len(videos) > 3:
                print(f"             ... and {len(videos)-3} more")
        else:
            print(f"  [EMPTY]     {m}  (no video >100MB, likely Jellyfin placeholder)")

    if any_real:
        print("ALREADY_EXISTS")
    else:
        print("NEEDS_DOWNLOAD")

if __name__ == "__main__":
    main()
