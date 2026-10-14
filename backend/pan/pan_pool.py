#!/usr/bin/env python3
"""Turn Xunlei cloud-drive resources into candidates for the same scoring pass.

A cloud-drive resource (a `pan.xunlei.com/s/...` share link, or something
already saved into the account) is a first-class candidate: it is scored by
`candidate_score` together with the magnets, and only the winner of that
ranking is worth the transfer + retrieve steps.

Two metadata moments:

* before transfer - only the share title (and optionally the size a search
  result advertised) is known, so the candidate is a coarse entry: `pre_transfer`
  is true and the missing file list is not treated as "no video".
* after transfer  - the client caches the account's file list in
  `~/Library/Application Support/Thunder/Database/com.xunlei.plugin.page.cloudspace.db`,
  which gives real names, sizes and extensions. `--from-cloud` reads that cache
  for an exact score.

Transfer and retrieve themselves are done by the Thunder client (see
`references/operations.md`, "Cloud Drive Resources"); this module only prepares
and scores the candidates.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3

from media_download_lib import GB, JUNK_EXTS, VIDEO_EXTS

CLOUD_DB = os.path.expanduser(
    "~/Library/Application Support/Thunder/Database/"
    "com.xunlei.plugin.page.cloudspace.db"
)
SHARE_PATTERN = re.compile(r"https?://pan\.xunlei\.com/s/([0-9A-Za-z_-]+)")
CODE_PATTERNS = [
    re.compile(r"(?:pwd|pass(?:word)?|提取码|密码)[=：:\s]*([0-9A-Za-z]{4,8})", re.IGNORECASE),
]


def parse_share_text(text):
    """Extract every share link plus pass code from a pasted blob."""
    text = text or ""
    default_code = ""
    for pattern in CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            default_code = match.group(1)
            break
    found = []
    for match in SHARE_PATTERN.finditer(text):
        tail = text[match.end() : match.end() + 60]
        code = default_code
        query = re.search(r"[?&]pwd=([0-9A-Za-z]{4,8})", text[match.end() - 0 : match.end() + 40])
        if query:
            code = query.group(1)
        found.append(
            {
                "share_url": match.group(0),
                "share_id": match.group(1),
                "pass_code": code,
            }
        )
    return found


def candidate_id(name, share_url=""):
    digest = hashlib.sha1(f"{name}|{share_url}".encode("utf-8")).hexdigest()
    return "PAN" + digest[:37].upper()


def read_cloud_index(db_path=CLOUD_DB):
    """Read the client's cached cloud file list (read-only)."""
    if not db_path or not os.path.exists(db_path):
        return []
    # The client keeps a WAL next to it; copy so we never touch its files.
    import shutil
    import tempfile

    workdir = tempfile.mkdtemp(prefix="cloudspace-index-")
    base = os.path.join(workdir, os.path.basename(db_path))
    rows = []
    try:
        for suffix in ("", "-wal", "-shm"):
            source = db_path + suffix
            if os.path.exists(source):
                shutil.copy2(source, base + suffix)
        con = sqlite3.connect(base)
        cur = con.execute(
            """
            SELECT id, parent_id, kind, name, size, file_extension, created_time
            FROM cloud_file_list_storer_table
            WHERE trashed = 0
            """
        )
        rows = [
            {
                "id": row[0],
                "parent_id": row[1],
                "kind": row[2],
                "name": row[3] or "",
                "size": int(row[4] or 0),
                "extension": (row[5] or "").lower(),
                "created_time": row[6] or "",
            }
            for row in cur.fetchall()
        ]
        con.close()
    except (OSError, sqlite3.Error):
        return []
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return rows


def cloud_candidates(rows, title, share_url="", pass_code=""):
    """Build candidates from cloud folders/files whose names match the title."""
    if not title:
        return []
    needle = title.lower()
    children = {}
    for row in rows:
        children.setdefault(row["parent_id"], []).append(row)

    def build(node, folder_name):
        entries = []
        for child in children.get(node["id"], []):
            if child["kind"] == "drive#folder":
                entries.extend(build(child, folder_name or child["name"]))
            else:
                entries.append(child)
        return entries

    candidates = []
    for row in rows:
        is_match = needle in row["name"].lower()
        if not is_match:
            continue
        if row["kind"] == "drive#folder":
            files = build(row, row["name"])
            label = row["name"]
        else:
            files = [row]
            label = row["name"]
        videos = [
            {
                "index": index,
                "path": entry["name"],
                "name": entry["name"],
                "size": entry["size"],
            }
            for index, entry in enumerate(files)
            if entry["extension"] in VIDEO_EXTS and entry["size"] >= 100 * 1024 ** 2
        ]
        junk = [
            entry
            for entry in files
            if entry["extension"] in JUNK_EXTS
        ]
        main_video = max(videos, key=lambda item: item["size"], default=None)
        candidates.append(
            {
                "id": candidate_id(label, share_url),
                "kind": "pan",
                "name": label,
                "share_url": share_url,
                "pass_code": pass_code,
                "in_cloud": True,
                "pre_transfer": False,
                "size_gb": sum(entry["size"] for entry in files) / GB,
                "nfiles": len(files),
                "videos": videos,
                "main_video_gb": (main_video["size"] / GB) if main_video else 0.0,
                "main_video_ext": (
                    os.path.splitext(main_video["name"])[1].lower()
                    if main_video
                    else ""
                ),
                "junk_files": len(junk),
                "junk_bytes": sum(entry["size"] for entry in junk),
                "source": "xunlei-cloud",
            }
        )
    return candidates


def share_candidates(entries):
    """Pre-transfer candidates built from share links + advertised titles."""
    candidates = []
    for entry in entries:
        name = entry.get("name") or entry.get("title") or entry.get("share_id") or ""
        candidate = {
            "id": candidate_id(name, entry.get("share_url", "")),
            "kind": "pan",
            "name": name,
            "share_url": entry.get("share_url", ""),
            "pass_code": entry.get("pass_code", ""),
            "in_cloud": False,
            "pre_transfer": True,
            "size_gb": float(entry.get("size_gb") or 0.0),
            "nfiles": int(entry.get("nfiles") or 0),
            "videos": entry.get("videos") or [],
            "main_video_gb": float(entry.get("main_video_gb") or 0.0),
            "main_video_ext": entry.get("main_video_ext") or "",
            "junk_files": int(entry.get("junk_files") or 0),
            "junk_bytes": int(entry.get("junk_bytes") or 0),
            "source": "xunlei-cloud",
        }
        candidates.append(candidate)
    return candidates


def load_pool(path):
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_pool(path, entries, query=""):
    if not path:
        return False
    payload = {
        "version": 1,
        "query": query,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "entries": entries,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print(f"POOL_WRITE_SKIPPED {exc}")
        return False


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("title")
    parser.add_argument("--share-url", action="append", default=[])
    parser.add_argument("--share-text", default="", help="粘贴的一整段分享文本")
    parser.add_argument("--candidate-file", help="JSONL: {name, share_url, pass_code, size_gb}")
    parser.add_argument("--from-cloud", action="store_true", help="读取本机云盘缓存")
    parser.add_argument("--cloud-db", default=CLOUD_DB)
    parser.add_argument("--pool-file", default="")
    parser.add_argument("--candidates-out", default="")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--no-pool", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    rows = read_cloud_index(args.cloud_db) if args.from_cloud else []
    candidates = cloud_candidates(rows, args.title)

    share_entries = []
    for url in args.share_url:
        parsed = parse_share_text(url) or [
            {"share_url": url, "share_id": "", "pass_code": ""}
        ]
        for item in parsed:
            share_entries.append({"name": args.title, **item})
    if args.share_text:
        for item in parse_share_text(args.share_text):
            share_entries.append({"name": args.title, **item})
    if args.candidate_file:
        try:
            with open(args.candidate_file, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    payload = json.loads(line)
                    parsed = parse_share_text(payload.get("share_url", ""))
                    merged = dict(payload)
                    if parsed and not merged.get("pass_code"):
                        merged["pass_code"] = parsed[0]["pass_code"]
                    merged.setdefault("name", args.title)
                    share_entries.append(merged)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"CANDIDATE_FILE_ERROR {exc}")
            return 2

    known = {(item["share_url"], item["name"]) for item in candidates}
    for entry in share_candidates(share_entries):
        if (entry["share_url"], entry["name"]) not in known:
            candidates.append(entry)

    print(
        "云盘候选: "
        + json.dumps(
            {
                "title": args.title,
                "from_cloud_index": bool(args.from_cloud),
                "cloud_rows": len(rows),
                "candidates": len(candidates),
                "in_cloud": sum(1 for item in candidates if item["in_cloud"]),
                "pre_transfer": sum(1 for item in candidates if item["pre_transfer"]),
            },
            ensure_ascii=False,
        )
    )
    for candidate in candidates:
        print(
            f"  {candidate['main_video_gb']:7.2f}GB "
            f"{candidate['nfiles']:>4}文件 "
            f"{'已转存' if candidate['in_cloud'] else '待转存'} "
            f"{candidate['name'][:56]}"
        )

    if args.pool_file and not args.no_pool:
        pool = load_pool(args.pool_file)
        for candidate in candidates:
            entry = pool.setdefault(candidate["id"], {})
            entry.update(candidate)
            entry["metadata_at"] = entry.get("metadata_at") or 0
        save_pool(args.pool_file, pool, args.title)

    if args.candidates_out:
        try:
            with open(args.candidates_out, "w", encoding="utf-8") as handle:
                json.dump(candidates, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            print(f"CANDIDATE_WRITE_SKIPPED {exc}")

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(
                    {"title": args.title, "candidates": candidates},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError as exc:
            print(f"JSON_WRITE_SKIPPED {exc}")

    if not candidates:
        print(
            "NO_PAN_CANDIDATE 可以加 --share-url 或 --from-cloud "
            "（后者读取本机迅雷云盘缓存）"
        )
        return 4
    print(
        "下一步: python3 scripts/probe_magnets.py \"{title}\" --extra-candidates "
        "{file} --json-out /tmp/{title}_pool.json".format(
            title=args.title,
            file=args.candidates_out or "/tmp/pan_candidates.json",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
