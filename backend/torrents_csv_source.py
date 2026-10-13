#!/usr/bin/env python3
"""Second discovery channel: torrents-csv, a public aggregate of DHT crawls.

Why this channel exists
-----------------------
`search_pool.py` talks to one index (`m.diao.im`) that returns no swarm fields
at all: its records carry only `hash/name/size/files/files_count/magnet_uri`,
so every candidate needs an aria2c metadata probe before it can be ranked for
swarm health. torrents-csv is the only free channel measured in the 2026-10-13
T2 audit that returns an info hash *and* a seeder count in the same response.

Measured 2026-10-13 (see /tmp/jmd-audit/t2-sources.md §1): `q=狂飙` returned 4
entries, all 4 the intended show, including a `[全39集][中文字幕].2160p` pack;
`q=漫长的季节` returned 2; `q=三体` returned 25.

The seeder count is a snapshot, not live
---------------------------------------
In the same audit `scraped_date` came back ~22.5 days behind wall clock. Every
entry therefore carries `seeders_snapshot_age_days` so the scoring side can
decide how much to trust it instead of treating it as a live swarm reading.

Interfaces (plain HTTP + JSON, no key)
--------------------------------------
* ``GET {base}?q=<query>&size=<n>`` -> ``{"torrents": [{"infohash", "name",
  "size_bytes", "created_unix", "seeders", "leechers", "completed",
  "scraped_date", "id"}], "next": ...}``
* offline dumps (CSV rows) through `parse_csv_rows` / `read_csv_dump`, so a
  local dump can be used without touching the network.

Zero third-party dependencies: urllib, json, csv only.
"""

import argparse
import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://torrents-csv.com/service/search"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JellyfinPool/1.0"
DEFAULT_TIMEOUT = 20
DEFAULT_RESULT_LIMIT = 100
MAX_RESULT_LIMIT = 1000
SOURCE_NAME = "torrents-csv"
HEX_HASH_LENGTH = 40
GB = 1024**3
CSV_FIELDS = ("info_hash", "infohash", "name", "size_bytes", "size", "file_count",
              "files_count", "seeders", "leechers", "created_unix", "created_at",
              "scraped_date")


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first(row, keys):
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def build_magnet(info_hash):
    """`magnet:?xt=urn:btih:<HASH>`; the DHT hash is the only thing this needs."""
    if not info_hash:
        return ""
    return f"magnet:?xt=urn:btih:{info_hash}"


def is_usable_hash(info_hash):
    """Accept the 40-hex form the rest of the backend already parses."""
    text = str(info_hash or "").strip()
    if len(text) != HEX_HASH_LENGTH:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in text)


def snapshot_age_days(scraped_unix, now=None):
    """How stale the seeder snapshot is, in days; None when the field is absent."""
    seconds = _as_int(scraped_unix, 0)
    if seconds <= 0:
        return None
    reference = time.time() if now is None else float(now)
    return round(max(reference - seconds, 0.0) / 86400.0, 2)


def snapshot_at(scraped_unix):
    """ISO-8601 UTC stamp of the snapshot, or None when the field is absent."""
    seconds = _as_int(scraped_unix, 0)
    if seconds <= 0:
        return None
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()


def normalise_entry(row, now=None):
    """One raw record (API or CSV row) -> the shared candidate entry shape.

    Keys are the ones the rest of the pipeline consumes after the merge step in
    `search_pool.py`: info_hash / name / size / file_count / seeders / sources /
    magnet. `file_count` stays None when the channel does not publish it --
    callers must treat None as "unknown", not as zero.
    """
    info_hash = str(_first(row, ("info_hash", "infohash")) or "").strip().upper()
    size = _as_int(_first(row, ("size", "size_bytes")), 0)
    raw_count = _first(row, ("file_count", "files_count"))
    created = _first(row, ("created_at", "created_unix"))
    scraped = row.get("scraped_date")
    return {
        "info_hash": info_hash,
        "name": str(row.get("name") or "").strip(),
        "size": size,
        "size_gb": round(size / GB, 3),
        "file_count": None if raw_count is None else _as_int(raw_count, 0),
        "seeders": None if row.get("seeders") is None else _as_int(row.get("seeders"), 0),
        "leechers": None if row.get("leechers") is None else _as_int(row.get("leechers"), 0),
        "created_at": created or None,
        "seeders_snapshot_unix": _as_int(scraped, 0) or None,
        "seeders_snapshot_at": snapshot_at(scraped),
        "seeders_snapshot_age_days": snapshot_age_days(scraped, now=now),
        "sources": [SOURCE_NAME],
        "magnet": build_magnet(info_hash),
    }


def parse_payload(payload, now=None):
    """API JSON payload -> entry list. Missing/odd shapes give an empty list."""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("torrents")
    if not isinstance(rows, list):
        return []
    entries = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry = normalise_entry(row, now=now)
        if is_usable_hash(entry["info_hash"]):
            entries.append(entry)
    return entries


def parse_csv_rows(rows, now=None):
    """CSV rows (csv.DictReader or plain mappings) -> entry list."""
    entries = []
    for row in rows or []:
        if isinstance(row, (list, tuple)):
            row = dict(zip(CSV_FIELDS, row))
        if not isinstance(row, dict):
            continue
        entry = normalise_entry(row, now=now)
        if is_usable_hash(entry["info_hash"]):
            entries.append(entry)
    return entries


def read_csv_dump(path, limit=0, now=None):
    """Read a local torrents-csv dump. Offline only -- never opens a socket."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            rows = []
            for index, row in enumerate(csv.DictReader(handle)):
                if limit and index >= limit:
                    break
                rows.append(row)
    except OSError:
        return []
    return parse_csv_rows(rows, now=now)


def _default_fetch(url, timeout):
    """Return (status, text). Network/HTTP failures come back as a status, not a raise."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except OSError:
            body = ""
        return exc.code, body
    except (OSError, ValueError) as exc:
        return 0, str(exc)


def search(query, limit=DEFAULT_RESULT_LIMIT, timeout=DEFAULT_TIMEOUT,
           base_url=DEFAULT_BASE_URL, fetch=None, now=None):
    """One HTTP call. Never raises: a dead channel degrades to an empty list.

    Returns ``{"status", "message", "query", "entries", "elapsed_secs"}``.
    `status` is 200 on success, the HTTP code on an error response, and 0 when
    the host could not be reached -- the same convention `search_pool.search_api`
    already uses, so callers can treat both channels the same way.
    """
    safe_limit = max(1, min(_as_int(limit, DEFAULT_RESULT_LIMIT), MAX_RESULT_LIMIT))
    params = urllib.parse.urlencode({"q": query, "size": safe_limit})
    url = f"{base_url}?{params}"
    sender = fetch or _default_fetch
    started = time.time()
    try:
        status, body = sender(url, timeout)
    except (OSError, ValueError) as exc:
        return {
            "status": 0, "message": str(exc), "query": query, "entries": [],
            "elapsed_secs": round(time.time() - started, 2),
        }
    elapsed = round(time.time() - started, 2)
    if status != 200:
        return {
            "status": status, "message": str(body)[:200], "query": query,
            "entries": [], "elapsed_secs": elapsed,
        }
    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        return {
            "status": 0, "message": f"bad JSON: {exc}", "query": query,
            "entries": [], "elapsed_secs": elapsed,
        }
    entries = parse_payload(payload, now=now)
    return {
        "status": 200, "message": "", "query": query,
        "entries": entries, "elapsed_secs": elapsed,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="torrents-csv discovery channel")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--csv-dump", default="", help="离线 dump 路径（不联网）")
    parser.add_argument("--json-out", default="")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.csv_dump:
        entries = read_csv_dump(args.csv_dump, limit=args.limit)
        result = {"status": 200, "message": "csv-dump", "query": args.query,
                  "entries": entries, "elapsed_secs": 0.0}
    else:
        result = search(args.query, limit=args.limit, timeout=args.timeout,
                        base_url=args.base_url)
    for entry in result["entries"]:
        print(
            f"{entry['size_gb']:8.2f}GB seeders={str(entry['seeders']):>6} "
            f"age={entry['seeders_snapshot_age_days']} {entry['name'][:70]}"
        )
    print("TORRENTS_CSV_RESULT " + json.dumps(
        {key: value for key, value in result.items() if key != "entries"},
        ensure_ascii=False,
    ))
    if args.json_out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            print(f"JSON_WRITE_SKIPPED {exc}")
    return 0 if result["status"] == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
