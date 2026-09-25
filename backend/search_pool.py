#!/usr/bin/env python3
"""Collect magnet candidates from the diao.im search API into the pool.

API facts (measured 2026-09-21, https://m.diao.im/api/search):

* `limit` is capped at 10. `limit=11` returns
  `{"data":null,"message":"limit: Number must be less than or equal to 10","status":400}`.
* `page` is ignored; `offset` is the real pager (offset 0/10/20 return
  disjoint sets).
* `sort` is ignored.
* Multiple words are AND-ed, and each extra word can zero the result set:
  `日落下的彩虹 S01` -> 0 hits, `日落下的彩虹 2026` -> 1 hit,
  `日落下的彩虹` -> 10 hits (including the complete ViuTV packs).

So the keyword policy here is: start from the shortest query (the bare title),
page through it with offset, and only then try one extra word at a time. A
three-word query such as `COURT 2026 ViuTV` is a precision bonus, never the
primary search.

Every hit carries a full file list, so candidates collected here need no
aria2c metadata probe; they are written straight into the probe pool.
"""

import argparse
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from media_download_lib import GB, JUNK_EXTS, POOL_DIR, VIDEO_EXTS

DEFAULT_BASE_URL = "https://m.diao.im/api/search"
MAX_LIMIT = 10
MIN_VIDEO_BYTES = 100 * 1024 ** 2
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JellyfinPool/1.0"

# Chinese packs are named "15集全" far more often than "S01".
COLLECTION_HINTS = ["全集", "Complete", "全", "S01"]
PACK_PATTERN = re.compile(r"集全|全集|全\d+集|全季|合集|Complete|Season\s*\d+", re.IGNORECASE)
MARKER_PATTERN = re.compile(r"S(\d{1,2})E(\d{1,3})", re.IGNORECASE)
EPISODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:EP|E|第)\s?0*(\d{1,3})(?![\d集全])", re.IGNORECASE)
# 纯编码关键词：S01 / S01E01 / E01。这类词不带语言属性，可以配任何片名
# （中文片名配 S01E01 是常见写法），所以严格匹配时不受「中英不混搭」限制。
CODE_HINT_RE = re.compile(r"(?:[Ss]\d{1,2}(?:[Ee]\d{1,2})?|[Ee]\d{1,3})")
DEFAULT_PLATFORMS = [
    "ViuTV",
    "TVBOXNOW",
    "Netflix",
    "Disney+",
    "HBO",
    "friDay",
    "myVideo",
    "KKTV",
]


def slugify(value, fallback="pool"):
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value or "").strip("-")
    return (slug or fallback)[:60]


def build_query_plan(
    title,
    alt_titles=(),
    year="",
    collection_hints=(),
    platforms=(),
    precision_query="",
    strict_match=False,
):
    """Short-first query ladder; each entry is tried until the pool is big enough.

    Tier order: bare title -> title + one word -> precision query. Every step
    adds at most one word, because a second word is what empties this API.
    """
    names = [name for name in [title, *alt_titles] if name]
    plan = []
    seen = set()

    def add(query, tier):
        query = " ".join((query or "").split())
        if not query or query in seen:
            return
        seen.add(query)
        plan.append({"query": query, "tier": tier})

    if strict_match:
        # 严格匹配：跳过基础片名/年份/平台档位，只用显式传入的整包词，
        # 避免基础片名把整部剧（含其它季/同名片）都带进来。
        for name in names:
            name_cjk = bool(re.search(r"[\u4e00-\u9fff]", name))
            for hint in collection_hints:
                hint_cjk = bool(re.search(r"[\u4e00-\u9fff]", hint))
                # 中英不混搭（S09 / S01E01 / E01 这类编码词可配任何名字，
                # 中文季词、集词只配中文名）
                if name_cjk != hint_cjk and not CODE_HINT_RE.fullmatch(hint):
                    continue
                add(f"{name} {hint}", "strict")
        return plan

    for name in names:
        add(name, "title")
    if year:
        for name in names:
            add(f"{name} {year}", "year")
    for hint in collection_hints:
        add(f"{names[0]} {hint}", "collection")
    for name in names[1:] or names:
        for platform in platforms:
            add(f"{name} {platform}", "platform")
    if precision_query:
        add(precision_query, "precision")
    return plan


def search_api(query, limit=MAX_LIMIT, offset=0, base_url=DEFAULT_BASE_URL, timeout=20):
    """One API call. Never send limit > 10; retry smaller if the API balks."""
    safe_limit = max(1, min(int(limit), MAX_LIMIT))
    attempts = sorted({safe_limit, 5, 2, 1}, reverse=True)
    attempts = [value for value in attempts if value <= safe_limit] or [safe_limit]

    last_error = ""
    for attempt_limit in attempts:
        params = {
            "keyword": query,
            "limit": attempt_limit,
            "offset": max(int(offset), 0),
        }
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except OSError:
                pass
            last_error = f"HTTP {exc.code} {body[:200]}"
            if exc.code == 400 and "limit" in body.lower():
                continue
            return {"status": exc.code, "message": last_error, "torrents": []}
        except (OSError, ValueError) as exc:
            return {"status": 0, "message": str(exc), "torrents": []}

        status = payload.get("status", 200)
        if status == 200:
            data = payload.get("data") or {}
            return {
                "status": 200,
                "keywords": data.get("keywords") or [],
                "torrents": data.get("torrents") or [],
                "limit": attempt_limit,
            }
        message = str(payload.get("message") or "")
        last_error = message
        if "limit" in message.lower():
            continue
        return {"status": status, "message": message, "torrents": []}
    return {"status": 400, "message": last_error, "torrents": []}


def torrent_to_candidate(item, query=""):
    """Convert one API torrent record into the pool/probe candidate shape."""
    files = []
    for entry in item.get("files") or []:
        path = entry.get("path") or ""
        files.append(
            {
                "index": int(entry.get("index") or 0),
                "path": path,
                "name": os.path.basename(path) or path,
                "size": int(entry.get("size") or 0),
            }
        )
    videos = [
        entry
        for entry in files
        if os.path.splitext(entry["name"])[1].lower() in VIDEO_EXTS
        and entry["size"] >= MIN_VIDEO_BYTES
    ]
    main_video = max(videos, key=lambda entry: entry["size"], default=None)
    junk = [
        entry
        for entry in files
        if os.path.splitext(entry["name"])[1].lower() in JUNK_EXTS
    ]
    info_hash = str(item.get("hash") or "").upper()
    return {
        "ih": info_hash,
        "name": item.get("name") or info_hash,
        "magnet": item.get("magnet_uri") or f"magnet:?xt=urn:btih:{info_hash}",
        "size_gb": float(item.get("size") or 0) / GB,
        "nfiles": int(item.get("files_count") or len(files)),
        "videos": videos,
        "main_video_gb": (main_video["size"] / GB) if main_video else 0.0,
        "main_video_ext": (
            os.path.splitext(main_video["name"])[1].lower() if main_video else ""
        ),
        "junk_files": len(junk),
        "junk_bytes": sum(entry["size"] for entry in junk),
        "created_at": item.get("created_at"),
        "source": "diao",
        "hit_query": query,
    }


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
        return True
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


def collect(args):
    """Run the query ladder and return (candidates, query_reports)."""
    pool = load_pool(args.pool_file)
    candidates = {}
    reports = []
    now = time.time()

    # 没传 --collection-hint 才用内置默认词；传了就用传的（严格按季依赖这一点，
    # 否则 argparse 的 append 会往默认词后面追加，把「全集」也一起跑了）
    hints = getattr(args, "collection_hint", None) or list(COLLECTION_HINTS)
    plan = build_query_plan(
        args.title,
        args.alt_title,
        year=args.year,
        collection_hints=hints,
        platforms=args.platform,
        precision_query=args.precision_query,
        strict_match=getattr(args, "strict_match", False),
    )
    if args.max_queries:
        plan = plan[: args.max_queries]
    request_limit = max(1, min(int(args.limit), MAX_LIMIT))

    for step in plan:
        query = step["query"]
        hits = 0
        fresh = 0
        for page in range(max(args.pages, 1)):
            offset = page * request_limit
            result = search_api(
                query,
                limit=request_limit,
                offset=offset,
                base_url=args.base_url,
                timeout=args.timeout,
            )
            if result.get("status") != 200:
                print(
                    "SEARCH_ERROR "
                    + json.dumps(
                        {
                            "query": query,
                            "offset": offset,
                            "status": result.get("status"),
                            "message": str(result.get("message"))[:200],
                        },
                        ensure_ascii=False,
                    )
                )
                break
            torrents = result.get("torrents") or []
            hits += len(torrents)
            for item in torrents:
                candidate = torrent_to_candidate(item, query)
                if not candidate["ih"]:
                    continue
                if candidate["ih"] in candidates:
                    continue
                candidates[candidate["ih"]] = candidate
                fresh += 1
            if len(torrents) < request_limit:
                break
            if args.sleep:
                time.sleep(args.sleep)
        reports.append(
            {
                "query": query,
                "tier": step["tier"],
                "pages": max(args.pages, 1),
                "hits": hits,
                "new": fresh,
            }
        )
        usable = [
            item for item in candidates.values() if item.get("main_video_gb")
        ]
        print(
            f"  [{step['tier']:<10}] {query[:46]:<46} "
            f"hits={hits:3d} new={fresh:3d} 可用={len(usable)}"
        )
        if len(usable) >= args.enough:
            print(f"  已达到 --enough {args.enough}，停止扩展关键词")
            break
        if args.sleep:
            time.sleep(args.sleep)

    for candidate in candidates.values():
        candidate["metadata_at"] = now
    return pool, candidates, reports


def candidate_markers(candidate, season=1):
    """Episode markers a candidate covers, e.g. {'S01E15'} or a pack's range.

    TVBOXNOW-style uploads put the episode number in the file name
    (`... EP15.mp4`) while the torrent name stays generic, so file names are
    checked first. Names flagged as a complete pack are not guessed at.
    """
    texts = [candidate.get("name") or ""]
    texts.extend(
        (entry.get("path") or entry.get("name") or "")
        for entry in candidate.get("videos") or []
    )
    blob = " ".join(texts)
    markers = {
        f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"
        for match in MARKER_PATTERN.finditer(blob)
    }
    if markers:
        return markers
    episode_text = " ".join(
        [
            (entry.get("name") or "")
            for entry in candidate.get("videos") or []
        ]
        + [candidate.get("name") or ""]
    )
    if PACK_PATTERN.search(candidate.get("name") or ""):
        return set()
    return {
        f"S{int(season):02d}E{int(number):02d}"
        for number in EPISODE_PATTERN.findall(episode_text)
        if int(number) > 0
    }


def write_episode_files(directory, candidates, season=1):
    """Split candidates into `<dir>/S01E15.magnets` files for series_queue."""
    if not directory:
        return {}
    per_marker = {}
    for candidate in candidates.values():
        for marker in candidate_markers(candidate, season):
            per_marker.setdefault(marker, []).append(candidate)
    written = {}
    try:
        os.makedirs(directory, exist_ok=True)
        for marker, items in per_marker.items():
            path = os.path.join(directory, f"{marker}.magnets")
            lines = [f"# {marker} search results"]
            for item in items:
                lines.append(item["magnet"])
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            written[marker] = len(items)
    except OSError as exc:
        print(f"EPISODE_FILES_SKIPPED {exc}")
    return written


def write_candidates_file(path, candidates, reports):
    if not path:
        return
    lines = ["# diao.im search results", "# 每行一个 magnet；# 为注释"]
    by_query = {}
    for candidate in candidates.values():
        by_query.setdefault(candidate.get("hit_query", ""), []).append(candidate)
    for report in reports:
        query = report["query"]
        items = by_query.get(query) or []
        lines.append(f"# query: {query} (hits={report['hits']})")
        for item in items:
            lines.append(item["magnet"])
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"CANDIDATE_WRITE_SKIPPED {exc}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("title")
    parser.add_argument("--alt-title", action="append", default=[])
    parser.add_argument("--year", default="")
    parser.add_argument(
        "--collection-hint",
        action="append",
        default=None,
        help="整包关键词，中文资源常用「全集」而不是 S01；不传则用内置列表。"
             "注意：传了就只跑这些词（严格按季时必须这样，否则默认词会把整部剧带进来）",
    )
    parser.add_argument(
        "--platform",
        action="append",
        default=[],
        help="平台/来源词，如 ViuTV、TVBOXNOW；默认用内置列表",
    )
    parser.add_argument(
        "--precision-query",
        default="",
        help="可选的三词精准查询，如 'COURT 2026 ViuTV'，仅作补充",
    )
    parser.add_argument(
        "--strict-match",
        action="store_true",
        help="只用显式传入的整包词查询（跳过基础片名/年份/平台档位与默认词），用于「只搜某一季」等严格场景",
    )
    parser.add_argument("--limit", type=int, default=MAX_LIMIT, help="每次请求条数，上限 10")
    parser.add_argument("--pages", type=int, default=2, help="每个关键词翻几页（offset 翻页）")
    parser.add_argument("--max-queries", type=int, default=8)
    parser.add_argument("--enough", type=int, default=12, help="可用候选达到该数量即停止扩展")
    parser.add_argument("--sleep", type=float, default=0.4)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--pool-file", default="")
    parser.add_argument("--candidates-out", default="")
    parser.add_argument(
        "--candidate-dir",
        default="",
        help="剧集：按 EP/S01E01 拆成 <dir>/S01E01.magnets 供 series_queue 使用",
    )
    parser.add_argument("--season", type=int, default=1)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--no-pool", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.limit > MAX_LIMIT:
        print(
            f"NOTE limit={args.limit} 超出 API 上限，已按 {MAX_LIMIT} 发送"
            "（limit>10 会返回 status=400）"
        )
        args.limit = MAX_LIMIT
    if not args.platform:
        args.platform = list(DEFAULT_PLATFORMS)
    slug = slugify(args.title)
    if not args.pool_file and not args.no_pool:
        args.pool_file = os.path.join(POOL_DIR, slug + ".json")
    if not args.candidates_out:
        args.candidates_out = f"/tmp/{slug}.magnets"
    if not args.json_out:
        args.json_out = f"/tmp/{slug}.search.json"

    print(
        "搜索来源: "
        + json.dumps(
            {
                "base_url": args.base_url,
                "limit": args.limit,
                "pages": args.pages,
                "max_queries": args.max_queries,
                "pool_file": args.pool_file or "disabled",
            },
            ensure_ascii=False,
        )
    )
    pool, candidates, reports = collect(args)

    usable = [
        item for item in candidates.values() if item.get("main_video_gb")
    ]
    no_video = [
        item for item in candidates.values() if not item.get("main_video_gb")
    ]
    merged = dict(pool)
    for candidate in candidates.values():
        entry = merged.setdefault(candidate["ih"], {})
        entry.update(
            {
                key: value
                for key, value in candidate.items()
                if key not in ("ih", "magnet", "hit_query", "source")
            }
        )
    saved = save_pool(args.pool_file, merged, args.title) if not args.no_pool else False
    write_candidates_file(args.candidates_out, candidates, reports)
    episode_files = write_episode_files(args.candidate_dir, candidates, args.season)

    payload = {
        "title": args.title,
        "alt_titles": args.alt_title,
        "year": args.year,
        "base_url": args.base_url,
        "limit": args.limit,
        "pages": args.pages,
        "queries": reports,
        "candidates": sorted(
            candidates.values(), key=lambda item: item.get("main_video_gb") or 0, reverse=True
        ),
        "no_video": [
            {"ih": item["ih"], "name": item["name"], "size_gb": item["size_gb"]}
            for item in no_video
        ],
        "pool_file": args.pool_file,
        "candidates_file": args.candidates_out,
        "episode_files": episode_files,
    }
    try:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"JSON_WRITE_SKIPPED {exc}")

    print("\n" + "=" * 92)
    for index, item in enumerate(
        sorted(usable, key=lambda entry: entry["main_video_gb"], reverse=True), start=1
    ):
        print(
            f"{index:>2} {item['main_video_gb']:7.2f}GB "
            f"{item['nfiles']:>4}文件 {item['name'][:62]}"
        )
    print("=" * 92)
    print(
        "SEARCH_RESULT "
        + json.dumps(
            {
                "queries": len(reports),
                "candidates": len(candidates),
                "usable": len(usable),
                "no_video": len(no_video),
                "pool_file": args.pool_file,
                "candidates_file": args.candidates_out,
                "candidate_dir": args.candidate_dir,
                "episode_markers": len(episode_files),
                "json_out": args.json_out,
                "pool_saved": saved,
            },
            ensure_ascii=False,
        )
    )
    if not usable:
        print("NO_USABLE_CANDIDATE 尝试用 --alt-title 加别名，或减少关键词再做一轮")
        return 4
    print(
        "下一步: python3 scripts/probe_magnets.py \"{title}\" --candidate-file {file} "
        "--pool-file {pool} --kind episode --speed-probe".format(
            title=args.title,
            file=args.candidates_out,
            pool=args.pool_file or "/tmp/pool.json",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
