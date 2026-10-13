#!/usr/bin/env python3
"""Collect magnet candidates from the diao.im search API plus torrents-csv.

API facts (measured 2026-09-21 / re-measured 2026-10-13, https://m.diao.im/api/search):

* `limit` is capped at 10. `limit=11` returns
  `{"data":null,"message":"limit: Number must be less than or equal to 10","status":400}`.
* `page` is ignored; `offset` is the real pager (offset 0/10/20 return
  disjoint sets).
* `sort` is ignored -- and so are `order`, `type` and `category`: five
  different sort/filter spellings returned the identical first record, so
  nothing but paging and keyword choice moves this index.
* Multiple words are AND-ed, and each extra word can zero the result set:
  `日落下的彩虹 S01` -> 0 hits, `日落下的彩虹 2026` -> 1 hit,
  `日落下的彩虹` -> 10 hits (including the complete ViuTV packs).
* The call signature is picky: no `User-Agent` gets a 403, and the keyword
  parameter must be spelled `keyword` (`q=` gets a 400). The response carries
  `data.has_more`, which the first implementation never read; see
  `fetch_query_pages` for how far paging is now allowed to go.

Keyword policy
--------------
Aliases/original titles (with the year) come first, then the bare title, then
one extra word at a time. The 2026-10-13 audit measured why: for a title built
from a common word (`狂飙`) the bare-title window was 0/10 the intended show,
while `The Knockout 2023` was 10/10 in a single ~1s request -- and blind paging
on that polluted keyword cost 115.4s for 9 pages and 3.3% genuine hits.

Paging policy
-------------
`--pages` (default 8), a wall-clock `--page-budget-secs` (default 45) and a
"two pages added nothing new" stop. Pages inside one wave are fetched
concurrently (`--page-workers`, default 4) because the budget is wall clock,
not request count: a measured 8-12s/page spell would otherwise cap a keyword at
3-4 pages. Per-request timeouts are clamped to the remaining budget, so the
wall clock stays inside the gate. The second source spends a reserved slice of
the same budget (`--extra-source-reserve-secs`, default 8), which is why a
single-keyword run cannot overshoot the gate by running both channels. A page
that fails is recorded and skipped (up to `--page-errors`, default 2): one slow
page used to abandon the whole keyword and lose the other pages with it.

Field contract with the probe and the scorer
--------------------------------------------
* `metadata_at` means one thing only: **the file list of this candidate has
  been verified by an actual metadata fetch**. It may therefore only be written
  next to a non-empty `videos` list. Candidates that arrive with a name but no
  file list (every torrents-csv entry: `videos: []`, `main_video_gb: 0.0`) are
  written *without* it, so `probe_magnets.pool_metadata` treats them as a cache
  miss and really fetches metadata instead of rejecting them as
  "no readable video file >=100MB".
* `external_seeders` (int) + `external_seeders_age_days` (float) describe a
  candidate's swarm size as reported by an external index, plus how stale that
  snapshot is. They are deliberately **not** `seeds`: `seeds` belongs to the
  announce measurement in `probe_magnets` and the two must never impersonate
  each other. Absent values stay `None`.
* Merging a fresh candidate into an existing pool entry never erases a known
  fact: an empty list / `0` / `None` does not overwrite a non-empty value
  (`merge_pool_entry`), and `metadata_at` is never refreshed by a merge.
* `--collapse-releases` still only marks (`release_key` / `duplicate_hashes`)
  unless it is passed explicitly, and the external channel name still travels in
  `external_sources` -- never in `sources`, which is the probe's extraction
  label space (`direct#N` / `pansou#N`).
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import torrents_csv_source

from media_download_lib import GB, JUNK_EXTS, POOL_DIR, VIDEO_EXTS

DEFAULT_BASE_URL = "https://m.diao.im/api/search"
MAX_LIMIT = 10
MIN_VIDEO_BYTES = 100 * 1024 ** 2
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JellyfinPool/1.0"
DEFAULT_PAGES = 8
DEFAULT_PAGE_BUDGET_SECS = 45.0
DEFAULT_PAGE_WORKERS = 4
DEFAULT_NO_NEW_PAGES = 2
DEFAULT_PAGE_ERRORS = 2
DEFAULT_PAGE_TIMEOUT = 20
# 下游 probe 的软预算，与控制台 PROBE_BUDGET_SECS 同值；只用于结尾提示文案。
PROBE_BUDGET_SECS_HINT = 720
DEFAULT_EXTRA_SOURCES = ("torrents-csv",)
# 第二阶段源在同一个 wall-clock 预算里预留的切片（秒）；剩不到 3s 就整段跳过。
EXTRA_SOURCE_MIN_SECS = 3.0
DEFAULT_EXTRA_SOURCE_RESERVE_SECS = 8.0

RESOLUTION_PATTERNS = (
    ("2160p", re.compile(r"2160p|\b4k\b|uhd|3840x2160", re.IGNORECASE)),
    ("1080p", re.compile(r"1080p|1920x1080|\bfhd\b", re.IGNORECASE)),
    ("720p", re.compile(r"720p|1280x720", re.IGNORECASE)),
    ("sd", re.compile(r"480p|576p|dvdrip|hdrip|hdtv|webrip|web-dl|bluray|bdrip", re.IGNORECASE)),
)
MARKETING_NOISE_RE = re.compile(
    r"www\.[a-z0-9.\-]+|[a-z0-9.\-]+\.(?:com|net|org|me|im|tv|cc|xyz)", re.IGNORECASE
)
RELEASE_NOISE_RE = re.compile(r"[\[\]【】()（）{}<>《》|/\\+_,:;!?*·—\-\s.]+")

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
    """Precision-first query ladder; each entry is tried until the pool is big enough.

    Tier order (non-strict): alias/year -> alias -> title/year -> title ->
    collection hints -> platform -> precision query. Aliases and the year come
    first because they are what buys precision on this index: a common-word
    title returned 0/10 intended hits while `The Knockout 2023` returned 10/10
    (2026-10-13 audit). The bare title is still in the ladder -- it is the only
    tier that works for titles with no alias and no year.

    Strict mode exists to keep a series search inside one season, so it never
    falls back to the bare title; it only runs `<name> <hint>` combinations,
    with aliases ahead of the primary title (an alias is less ambiguous).
    """
    names = [name for name in [title, *alt_titles] if name]
    aliases = names[1:]
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
        for name in aliases + names[:1]:
            name_cjk = bool(re.search(r"[\u4e00-\u9fff]", name))
            for hint in collection_hints:
                hint_cjk = bool(re.search(r"[\u4e00-\u9fff]", hint))
                # 中英不混搭（S09 / S01E01 / E01 这类编码词可配任何名字，
                # 中文季词、集词只配中文名）
                if name_cjk != hint_cjk and not CODE_HINT_RE.fullmatch(hint):
                    continue
                add(f"{name} {hint}", "strict")
        return plan

    if year:
        for name in aliases:
            add(f"{name} {year}", "alias-year")
    for name in aliases:
        add(name, "alias")
    if year:
        add(f"{title} {year}", "year")
    for name in names:
        add(name, "title")
    for hint in collection_hints:
        add(f"{names[0]} {hint}", "collection")
    for name in aliases or names:
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
            return {"status": exc.code, "message": last_error, "torrents": [], "has_more": False}
        except (OSError, ValueError) as exc:
            return {"status": 0, "message": str(exc), "torrents": [], "has_more": False}

        status = payload.get("status", 200)
        if status == 200:
            data = payload.get("data") or {}
            return {
                "status": 200,
                "keywords": data.get("keywords") or [],
                "torrents": data.get("torrents") or [],
                "limit": attempt_limit,
                # The index keeps reporting how much is left; paging stops on it.
                "has_more": bool(data.get("has_more")),
            }
        message = str(payload.get("message") or "")
        last_error = message
        if "limit" in message.lower():
            continue
        return {"status": status, "message": message, "torrents": [], "has_more": False}
    return {"status": 400, "message": last_error, "torrents": [], "has_more": False}


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


def torrents_csv_to_candidate(entry, query=""):
    """One torrents-csv entry -> the same candidate shape diao hits use.

    The channel publishes no file list, so `videos`/`main_video_gb` stay empty
    and the probe stage fills them in. What it does publish is the seeder count,
    carried here as `external_seeders` plus `external_seeders_age_days` (the
    snapshot was measured ~22.5 days behind wall clock) -- never as `seeds`,
    which the announce measurement owns. `torrents_csv_source` calls the same
    two numbers `seeders` / `seeders_snapshot_age_days` in its own entry shape;
    this function is the boundary where they get their pool-side names.

    The channel name goes to `external_sources`, NOT `sources`: `sources` is
    already taken by the probe's extraction labels (`direct#N` / `pansou#N`),
    and overwriting it would corrupt that contract.

    No `metadata_at` is set: without a file list this candidate has not passed
    the metadata gate, and pretending otherwise would make the probe skip the
    fetch and then reject it as "no readable video file".
    """
    info_hash = str(entry.get("info_hash") or "").upper()
    if not info_hash:
        return None
    external_seeders = entry.get("seeders")
    external_age = entry.get("seeders_snapshot_age_days")
    return {
        "ih": info_hash,
        "name": entry.get("name") or info_hash,
        "magnet": entry.get("magnet") or f"magnet:?xt=urn:btih:{info_hash}",
        "size_gb": float(entry.get("size") or 0) / GB,
        "nfiles": int(entry.get("file_count") or 0),
        "videos": [],
        "main_video_gb": 0.0,
        "main_video_ext": "",
        "junk_files": 0,
        "junk_bytes": 0,
        "created_at": entry.get("created_at"),
        "source": torrents_csv_source.SOURCE_NAME,
        "hit_query": query,
        "external_seeders": None if external_seeders is None else int(external_seeders),
        "external_seeders_age_days": None if external_age is None else float(external_age),
        "external_sources": list(entry.get("sources") or [torrents_csv_source.SOURCE_NAME]),
    }


def release_resolution(name):
    """Coarse resolution tag used as one dimension of the de-dupe key."""
    text = name or ""
    for tag, pattern in RESOLUTION_PATTERNS:
        if pattern.search(text):
            return tag
    return "unknown"


def normalise_release_name(name):
    """Strip marketing domains/brackets so two uploads of one release compare equal.

    Deliberately conservative: release-group tags survive (they are plain
    tokens). Dropping them would merge genuinely different encodes into a single
    "duplicate", which is the opposite of what this key is for.
    """
    text = MARKETING_NOISE_RE.sub(" ", name or "")
    text = os.path.splitext(text)[0]
    text = RELEASE_NOISE_RE.sub(" ", text)
    return " ".join(text.lower().split())


def release_key(candidate):
    """De-dupe dimension added on top of the info-hash key.

    The pool is keyed by info hash, and one release routinely arrives as 5+
    different hashes (the audit saw `[第31-36集]`, `全39集` and `Complete`
    variants of one season). Recording `normalised name + resolution + source`
    makes those siblings visible instead of silently competing as unrelated
    entries.
    """
    return "|".join(
        (
            normalise_release_name(candidate.get("name") or ""),
            release_resolution(candidate.get("name") or ""),
            str(candidate.get("source") or ""),
        )
    )


def collapse_release_duplicates(candidates):
    """Keep one representative per `release_key`; alternatives are recorded.

    Off by default in `collect`. Measured 2026-10-13: collapsing on the L1
    sample would take a keyword below the 3x candidate target the same change
    is meant to deliver, so the dimension is reported (`release_groups`) and the
    alternates are listed on the representative instead of being dropped.
    """
    kept = {}
    dropped = {}
    for info_hash, candidate in candidates.items():
        key = candidate.get("release_key") or release_key(candidate)
        representative = kept.get(key)
        if representative is None:
            kept[key] = candidate
            continue
        siblings = representative.setdefault("duplicate_hashes", [])
        siblings.append(info_hash)
        representative.setdefault("duplicate_names", []).append(candidate.get("name") or info_hash)
        dropped.setdefault(key, []).append(info_hash)
    return kept, dropped


class Deadline:
    """Wall-clock budget shared by the pager and the second source.

    The clock is injectable so the budget gate can be proven without sleeping.
    """

    def __init__(self, budget_secs, clock=time.monotonic):
        self.clock = clock
        self.budget_secs = max(float(budget_secs), 0.0)
        self.started = clock()

    def elapsed(self):
        return self.clock() - self.started

    def remaining(self):
        return self.budget_secs - self.elapsed()

    def expired(self):
        return self.remaining() <= 0


def fetch_query_pages(
    query,
    request_limit=MAX_LIMIT,
    pages=DEFAULT_PAGES,
    budget=None,
    base_url=DEFAULT_BASE_URL,
    timeout=DEFAULT_PAGE_TIMEOUT,
    search=search_api,
    sleep=0.0,
    workers=DEFAULT_PAGE_WORKERS,
    no_new_pages=DEFAULT_NO_NEW_PAGES,
    error_limit=DEFAULT_PAGE_ERRORS,
    seen_hashes=None,
    hashes_of=None,
):
    """Page one query until the index says stop, the budget runs out, or nothing is new.

    Returns ``{"pages", "requests", "errors", "stopped_by", "elapsed_secs"}``,
    where each page is ``{"offset", "torrents", "has_more", "fresh"}``.
    `fresh` counts hashes not already in `seen_hashes`, and `seen_hashes` is
    updated in place so the caller's candidate dict stays the single source of
    truth for de-duplication.

    Pages are fetched in waves of `workers` concurrent requests, but evaluated in
    offset order, so the stop conditions behave exactly as they would
    sequentially -- at the cost of the (already paid for) extra pages in the
    final wave. Per-request timeout is clamped to the remaining budget, which is
    what keeps a slow index from pushing the wall clock past the gate.
    """
    hash_reader = hashes_of or (lambda item: str(item.get("hash") or "").upper())
    seen = seen_hashes if seen_hashes is not None else set()
    offsets = [index * request_limit for index in range(max(int(pages), 1))]
    worker_count = max(1, min(int(workers or 1), len(offsets)))
    started_at = time.monotonic()
    collected = []
    errors = []
    requests = 0
    stopped_by = "pages_exhausted"
    no_new_streak = 0
    error_count = 0

    def one_page(offset, page_timeout):
        return search(query, limit=request_limit, offset=offset, base_url=base_url, timeout=page_timeout)

    for wave_start in range(0, len(offsets), worker_count):
        if budget is not None and budget.expired():
            stopped_by = "budget"
            break
        wave = offsets[wave_start:wave_start + worker_count]
        page_timeout = float(timeout)
        if budget is not None:
            page_timeout = max(1.0, min(page_timeout, budget.remaining()))
        if wave_start and sleep:
            time.sleep(sleep)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(wave)) as pool:
            futures = {offset: pool.submit(one_page, offset, page_timeout) for offset in wave}
            results = {}
            for offset, future in futures.items():
                try:
                    results[offset] = future.result()
                except (OSError, ValueError, RuntimeError) as exc:
                    results[offset] = {"status": 0, "message": str(exc), "torrents": [], "has_more": False}
                requests += 1
        stop = False
        for offset in wave:
            result = results[offset]
            status = result.get("status")
            if status != 200:
                errors.append({"offset": offset, "status": status,
                               "message": str(result.get("message"))[:200]})
                error_count += 1
                # 单页失败丢掉 10 条候选，但不该拖垮整个关键词：继续翻，
                # 直到失败页数达到上限（默认 2）才停。
                if error_count >= error_limit:
                    stopped_by = "error"
                    stop = True
                    break
                continue
            torrents = result.get("torrents") or []
            fresh = 0
            for item in torrents:
                info_hash = hash_reader(item)
                if not info_hash or info_hash in seen:
                    continue
                seen.add(info_hash)
                fresh += 1
            collected.append({
                "offset": offset,
                "torrents": torrents,
                "has_more": bool(result.get("has_more")),
                "fresh": fresh,
            })
            no_new_streak = no_new_streak + 1 if fresh == 0 else 0
            if not result.get("has_more"):
                stopped_by = "has_more"
                stop = True
                break
            if no_new_streak >= max(int(no_new_pages), 1):
                stopped_by = "no_new"
                stop = True
                break
        if stop:
            break

    elapsed = budget.elapsed() if budget is not None else time.monotonic() - started_at
    return {
        "pages": collected,
        "requests": requests,
        "errors": errors,
        "stopped_by": stopped_by,
        "elapsed_secs": round(elapsed, 2),
    }


# 写进池条目时要丢掉的候选侧字段：magnet 由池子/探针各自重建，来源标签属于候选而不是池。
POOL_MERGE_SKIP_KEYS = ("ih", "magnet", "hit_query", "source")
# 这些列表字段按并集合并，而不是整体替换。
POOL_MERGE_UNION_KEYS = ("external_sources",)
# 「补字段」白名单：swarm 读数 + 来源 + 索引时间 + 去重维度。
# 一个只知道名字、不知道文件清单的候选（每个外部条目都是这样）只能用这些字段去补，
# 不能改写已经由 L1 描述、或已经由探针核实过的标识与文件清单。
PROVENANCE_MERGE_KEYS = (
    "external_seeders",
    "external_seeders_age_days",
    "external_sources",
    "created_at",
    "release_key",
    "duplicate_hashes",
)
EXTERNAL_MERGE_KEYS = PROVENANCE_MERGE_KEYS


def merge_external_entries(candidates, entries, query=""):
    """Merge torrents-csv entries into the candidate dict without clobbering diao hits.

    A hash already present keeps its diao metadata (file list, size) and only
    gains the swarm fields; `fresh` counts the candidates this channel added on
    its own.
    """
    fresh = 0
    for entry in entries or []:
        candidate = torrents_csv_to_candidate(entry, query)
        if not candidate:
            continue
        existing = candidates.get(candidate["ih"])
        if existing is None:
            candidates[candidate["ih"]] = candidate
            fresh += 1
            continue
        merge_pool_entry(existing, candidate, allow_metadata_at=False,
                         keys=EXTERNAL_MERGE_KEYS)
    return fresh


def is_empty_value(value):
    """True for the values that must never overwrite a known non-empty one."""
    if value is None:
        return True
    if isinstance(value, (list, dict, str, tuple)):
        return len(value) == 0
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    return False


def merge_pool_entry(existing, incoming, allow_metadata_at=True, keys=None):
    """Fold a fresh candidate into an existing pool entry without erasing facts.

    Three rules, the first two from the I4 integration review (F2):

    * an empty list / `0` / `None` never overwrites a non-empty value, because
      the second channel knows nothing about file lists and used to blank out
      the `videos`/`main_video_gb`/`nfiles` a previous probe had established;
    * `metadata_at` is not refreshed by a merge (`allow_metadata_at=False` from
      the external path, and pool merges keep the stamp the probe wrote), so a
      merge cannot silently extend the metadata cache TTL of an untouched file
      list;
    * `keys` narrows the merge to an allow-list. The external channel passes its
      swarm/provenance keys only: an index that knows a name but no file list
      must not rewrite the identity of a candidate the L1 channel described in
      full.

    List-valued provenance fields (`external_sources`) are unioned instead of
    replaced.
    """
    for key, value in incoming.items():
        if key in POOL_MERGE_SKIP_KEYS or (keys is not None and key not in keys):
            continue
        if key == "metadata_at" and (not allow_metadata_at or key in existing):
            continue
        if isinstance(value, list) and key in POOL_MERGE_UNION_KEYS:
            merged = list(existing.get(key) or [])
            for item in value:
                if item not in merged:
                    merged.append(item)
            if merged:
                existing[key] = merged
            continue
        if is_empty_value(value) and not is_empty_value(existing.get(key)):
            continue
        existing[key] = value
    return existing


def collect_external_source(args, names, candidates, budget):
    """Second discovery channel, inside the remainder of the same budget.

    Never raises and never touches the L1 path: an unreachable channel is
    reported as a status and contributes zero candidates.
    """
    source = args.extra_source
    report = {
        "source": source, "status": None, "queries": 0, "entries": 0,
        "new": 0, "elapsed_secs": 0.0, "message": "",
        # Reported so the caller (and its tests) can see which slice this
        # channel was actually allowed to spend, not which one it was meant to.
        "budget_secs": round(budget.budget_secs, 2) if budget is not None else None,
    }
    if not source:
        report["status"] = "disabled"
        return report
    if source != torrents_csv_source.SOURCE_NAME:
        report["status"] = "unknown-source"
        report["message"] = source
        return report
    if budget is None or budget.remaining() < EXTRA_SOURCE_MIN_SECS:
        report["status"] = "budget-exhausted"
        return report
    started = time.time()
    seen_queries = []
    for name in names:
        remaining = budget.remaining()
        if remaining < EXTRA_SOURCE_MIN_SECS:
            report["message"] = "budget exhausted before every alias was queried"
            break
        if name in seen_queries:
            continue
        seen_queries.append(name)
        result = torrents_csv_source.search(
            name,
            limit=args.extra_source_limit,
            timeout=max(EXTRA_SOURCE_MIN_SECS, min(float(args.extra_source_timeout), remaining)),
            base_url=args.extra_source_base_url,
        )
        report["queries"] += 1
        report["status"] = result.get("status")
        report["message"] = str(result.get("message") or "")[:200]
        if result.get("status") != 200:
            # 一个查询失败就停止该通道，但保留已拿到的条目；L1 不受影响。
            break
        entries = result.get("entries") or []
        report["entries"] += len(entries)
        report["new"] += merge_external_entries(candidates, entries, name)
    report["elapsed_secs"] = round(time.time() - started, 2)
    if report["status"] is None:
        report["status"] = "skipped"
    return report


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
    """Run the query ladder and return (pool, candidates, reports, external_reports)."""
    pool = load_pool(args.pool_file)
    candidates = {}
    reports = []
    external_reports = []
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

    extra_source = getattr(args, "extra_source", "")
    total_budget = float(getattr(args, "page_budget_secs", DEFAULT_PAGE_BUDGET_SECS))
    reserve = 0.0
    if extra_source:
        requested = float(getattr(args, "extra_source_reserve_secs", 0.0) or 0.0)
        reserve = requested if requested > 0 else min(
            float(getattr(args, "extra_source_timeout", DEFAULT_EXTRA_SOURCE_RESERVE_SECS)),
            DEFAULT_EXTRA_SOURCE_RESERVE_SECS,
        )
        # 预留切片不能吃掉整个预算，否则翻页永远没得跑。
        reserve = min(reserve, max(total_budget - 1.0, 0.0))
    page_budget_secs = max(total_budget - reserve, 1.0)
    seen_hashes = set()

    try:
        for step in plan:
            query = step["query"]
            deadline = Deadline(page_budget_secs)
            outcome = fetch_query_pages(
                query,
                request_limit=request_limit,
                pages=getattr(args, "pages", DEFAULT_PAGES),
                budget=deadline,
                base_url=args.base_url,
                timeout=getattr(args, "timeout", DEFAULT_PAGE_TIMEOUT),
                sleep=args.sleep,
                workers=getattr(args, "page_workers", DEFAULT_PAGE_WORKERS),
                no_new_pages=getattr(args, "no_new_pages", DEFAULT_NO_NEW_PAGES),
                error_limit=getattr(args, "page_errors", DEFAULT_PAGE_ERRORS),
                seen_hashes=seen_hashes,
            )
            hits = 0
            fresh = 0
            for page in outcome["pages"]:
                hits += len(page["torrents"])
                for item in page["torrents"]:
                    candidate = torrent_to_candidate(item, query)
                    if not candidate["ih"]:
                        continue
                    if candidate["ih"] in candidates:
                        continue
                    candidate["release_key"] = release_key(candidate)
                    candidate["duplicate_hashes"] = []
                    candidates[candidate["ih"]] = candidate
                    fresh += 1
            for error in outcome["errors"]:
                print("SEARCH_ERROR " + json.dumps(
                    dict(error, query=query), ensure_ascii=False))

            reports.append(
                {
                    "query": query,
                    "tier": step["tier"],
                    "pages": len(outcome["pages"]),
                    "requests": outcome["requests"],
                    "hits": hits,
                    "new": fresh,
                    "has_more": outcome["pages"][-1]["has_more"] if outcome["pages"] else False,
                    "stopped_by": outcome["stopped_by"],
                    "elapsed_secs": round(deadline.elapsed(), 2),
                }
            )
            # N3：每完成一个关键词就把已完成的部分原子落盘（池 + 候选磁力）。
            land_progress(args, pool, candidates, reports, now=now)
            usable = [
                item for item in candidates.values() if item.get("main_video_gb")
            ]
            print(
                f"  [{step['tier']:<10}] {query[:46]:<46} "
                f"hits={hits:3d} new={fresh:3d} 可用={len(usable)} "
                f"页={len(outcome['pages'])} {outcome['stopped_by']} "
                f"{deadline.elapsed():.1f}s"
            )
            if len(usable) >= args.enough:
                print(f"  已达到 --enough {args.enough}，停止扩展关键词")
                break
            if args.sleep:
                time.sleep(args.sleep)

        if extra_source:
            names = [name for name in [args.title, *(args.alt_title or [])] if name]
            external_reports.append(
                collect_external_source(args, names, candidates, Deadline(reserve))
            )
            for report in external_reports:
                print("EXTRA_SOURCE " + json.dumps(report, ensure_ascii=False))
    finally:
        # 手动中断（KeyboardInterrupt）/异常路径也兜一次：pager 已经返回的那几个
        # 关键词的候选不能因为后面被杀而丢掉。正常路径这里等同再写一次同样内容。
        land_progress(args, pool, candidates, reports, now=now)

    stamp_candidates(candidates, now)
    return pool, candidates, reports, external_reports


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
    """Write every candidate magnet, grouped by the query that found it.

    F3: the groups used to be exactly the L1 `reports` queries, so any candidate
    whose `hit_query` was not among them -- every extra-source candidate, which
    is queried by bare title/alias -- silently vanished from the file. The probe
    reads candidates **only** from this file, so those magnets were lost even
    though `SEARCH_RESULT` counted them. Orphan groups are now written too, so
    the number of magnet lines equals the number of candidates.

    写入走 tmp + `os.replace`（与 `save_pool` 同一手法）：这个文件会被渐进落盘
    反复重写，而 probe 只从它读候选，半写的文件等于一整轮候选消失。返回是否写入成功。
    """
    if not path:
        return False
    lines = ["# search results (diao.im + extra sources)", "# 每行一个 magnet；# 为注释"]
    by_query = {}
    for candidate in candidates.values():
        by_query.setdefault(candidate.get("hit_query", ""), []).append(candidate)
    reported = {report["query"] for report in reports}
    for report in reports:
        query = report["query"]
        items = by_query.get(query) or []
        lines.append(f"# query: {query} (hits={report['hits']})")
        for item in items:
            lines.append(item["magnet"])
    for query in by_query:
        if query in reported:
            continue
        items = by_query[query] or []
        lines.append(f"# extra-source query: {query or '(none)'} (n={len(items)})")
        for item in items:
            lines.append(item["magnet"])
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print(f"CANDIDATE_WRITE_SKIPPED {exc}")
        return False


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
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES, help="每个关键词最多翻几页（offset 翻页）")
    parser.add_argument(
        "--page-budget-secs",
        type=float,
        default=DEFAULT_PAGE_BUDGET_SECS,
        help="单个关键词的墙钟上限（含第二阶段源的预留切片）；到点即停",
    )
    parser.add_argument(
        "--page-workers",
        type=int,
        default=DEFAULT_PAGE_WORKERS,
        help="同一关键词并发抓几页（预算是墙钟不是请求数，慢索引下靠并发保住翻页量）",
    )
    parser.add_argument(
        "--no-new-pages",
        type=int,
        default=DEFAULT_NO_NEW_PAGES,
        help="连续几页没有新 info hash 就停止翻页",
    )
    parser.add_argument(
        "--page-errors",
        type=int,
        default=DEFAULT_PAGE_ERRORS,
        help="一个关键词最多容忍几页请求失败（失败页跳过，不拖垮整个关键词）",
    )
    parser.add_argument("--max-queries", type=int, default=8)
    parser.add_argument("--enough", type=int, default=12, help="可用候选达到该数量即停止扩展")
    parser.add_argument("--sleep", type=float, default=0.4)
    parser.add_argument("--timeout", type=int, default=DEFAULT_PAGE_TIMEOUT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--extra-source",
        default=torrents_csv_source.SOURCE_NAME,
        help="第二阶段发现通道（仅支持 torrents-csv）；传空字符串关闭",
    )
    parser.add_argument(
        "--extra-source-base-url",
        default=torrents_csv_source.DEFAULT_BASE_URL,
    )
    parser.add_argument("--extra-source-limit", type=int, default=torrents_csv_source.DEFAULT_RESULT_LIMIT)
    parser.add_argument("--extra-source-timeout", type=int, default=torrents_csv_source.DEFAULT_TIMEOUT)
    parser.add_argument(
        "--extra-source-reserve-secs",
        type=float,
        default=DEFAULT_EXTRA_SOURCE_RESERVE_SECS,
        help="从 --page-budget-secs 里给第二阶段源预留的秒数",
    )
    parser.add_argument(
        "--collapse-releases",
        action="store_true",
        help="按「规范化片名+分辨率+来源」合并同版本条目（默认只标记不合并，见 collapse_release_duplicates）",
    )
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


def stamp_candidates(candidates, now):
    """给本轮候选补上去重维度，并按 F1 规则决定要不要盖 `metadata_at`。

    `metadata_at` 只代表「文件清单已核实」：外部通道的条目 `videos` 为空，不盖这个
    章，probe 才会真的去 swarm 抓 metadata，而不是把空清单当命中的缓存再以
    「没有可读视频」剔掉。渐进落盘前也要先跑一遍，否则被杀时落进池子的条目会
    缺这枚章（下一轮要白抓一遍 metadata）。
    """
    for candidate in candidates.values():
        if candidate.get("videos"):
            candidate["metadata_at"] = now
        # 外部通道直接插进来的候选要补上同样的去重维度（否则 release_groups 会把
        # 它们算成一堆 None）。
        candidate.setdefault("release_key", release_key(candidate))
        candidate.setdefault("duplicate_hashes", [])


def land_progress(args, pool, candidates, reports, now=None):
    """把「已经拿到的那部分」原子落盘一次：池 + 候选磁力。

    为什么需要它（t15/N3）：L1 阶梯的最坏墙钟是
    `--max-queries × --page-budget-secs`（控制台传 12 × 36s = 432s），而控制台给
    这个阶段的硬超时是 `SEARCH_POOL_TIMEOUT = 600s`；中途被杀（rc=124）或抛
    KeyboardInterrupt 时，如果只在 `main()` 末尾落盘，已经翻出来的候选整段丢
    （t15 的零联网复现：池文件与候选文件都不存在）。所以每完成一个关键词就落一次。

    成本是几十 KB 的顺序写 + 一次 `os.replace`，相对每个关键词的网络耗时可以忽略。
    失败只打印不抛出：落盘不该把搜索打断（`save_pool` / `write_candidates_file`
    各自已经吞掉 OSError）。

    `now` 非空时先 `stamp_candidates()`（渐进落盘的调用方传 collect 的 `now`）。
    返回 (pool_saved, candidates_written)，便于调用方/测试核对。
    """
    if now is not None:
        stamp_candidates(candidates, now)
    merged = merge_candidates_into_pool(pool, candidates)
    pool_saved = False
    if not getattr(args, "no_pool", False):
        pool_saved = save_pool(args.pool_file, merged, args.title)
    written = write_candidates_file(args.candidates_out, candidates, reports)
    return pool_saved, written


def merge_candidates_into_pool(pool, candidates):
    """把本轮候选合并进池子条目；空值不覆盖已知事实（F2 的规则）。

    `main()` 与渐进落盘共用这一份实现，避免两处各自实现一套合并规则。
    """
    merged = dict(pool)
    for candidate in candidates.values():
        fields = {
            key: value
            for key, value in candidate.items()
            if key not in POOL_MERGE_SKIP_KEYS
        }
        entry = merged.get(candidate["ih"])
        if entry is None:
            # 新条目：candidate 自己就是它已知的全部（有文件清单才带 metadata_at）。
            merged[candidate["ih"]] = dict(fields)
            continue
        # F2：已有条目只被「补充」。空值不覆盖已知文件清单，metadata_at 不刷新；
        # 若池里那份文件清单已经核实过，而这次来的候选根本没有文件清单，则只补
        # swarm/来源字段——一个只知道名字的索引不该改写已核实的标识。
        verified_list = bool(entry.get("videos")) and bool(entry.get("metadata_at"))
        if verified_list and not fields.get("videos"):
            merge_pool_entry(entry, fields, allow_metadata_at=False,
                             keys=PROVENANCE_MERGE_KEYS)
        else:
            merge_pool_entry(entry, fields, allow_metadata_at=False)
    return merged


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
                "page_budget_secs": args.page_budget_secs,
                "page_workers": args.page_workers,
                "max_queries": args.max_queries,
                "extra_source": args.extra_source or "disabled",
                "pool_file": args.pool_file or "disabled",
            },
            ensure_ascii=False,
        )
    )
    # collect() 自己带 try/finally（每完成一个关键词落一次盘），所以这里只在它
    # **成功返回之后**再包一层：collect 抛异常时不能进这个 finally——那时局部名
    # 还没绑定，用空字典落一次盘会把刚才的渐进落盘成果清成空文件（实测过）。
    pool, candidates, reports, external_reports = collect(args)
    try:

        if args.collapse_releases:
            collapsed, dropped = collapse_release_duplicates(candidates)
            print("COLLAPSE_RELEASES " + json.dumps(
                {"groups": len(collapsed), "dropped": sum(len(v) for v in dropped.values())},
                ensure_ascii=False))
            candidates = collapsed

        usable = [
            item for item in candidates.values() if item.get("main_video_gb")
        ]
        no_video = [
            item for item in candidates.values() if not item.get("main_video_gb")
        ]
        merged = merge_candidates_into_pool(pool, candidates)
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
            "page_budget_secs": args.page_budget_secs,
            "queries": reports,
            "external_sources": external_reports,
            "release_groups": len({item.get("release_key") for item in candidates.values()}),
            "candidates": sorted(
                candidates.values(), key=lambda item: item.get("main_video_gb") or 0, reverse=True
            ),
            "no_video": [
                {
                    "ih": item["ih"],
                    "name": item["name"],
                    "size_gb": item["size_gb"],
                    "external_seeders": item.get("external_seeders"),
                    "external_seeders_age_days": item.get("external_seeders_age_days"),
                }
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
        external = [
            item for item in candidates.values() if item.get("external_sources")
        ]
        print(
            "SEARCH_RESULT "
            + json.dumps(
                {
                    "queries": len(reports),
                    "candidates": len(candidates),
                    "usable": len(usable),
                    "no_video": len(no_video),
                    "external": len(external),
                    "with_external_seeders": sum(
                        1 for item in candidates.values()
                        if item.get("external_seeders") is not None
                    ),
                    "release_groups": payload["release_groups"],
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
        if not usable and not external:
            print("NO_USABLE_CANDIDATE 尝试用 --alt-title 加别名，或减少关键词再做一轮")
            return 4
        print(
            "下一步: python3 scripts/probe_magnets.py \"{title}\" --candidate-file {file} "
            "--pool-file {pool} --kind episode --budget-secs {budget}".format(
                title=args.title,
                file=args.candidates_out,
                pool=args.pool_file or "/tmp/pool.json",
                # 与 console_server.PROBE_BUDGET_SECS 同一口径。不要在这条提示里
                # 加 --speed-probe：那是已废弃的试跑测速路径（控制台已移除该开关）。
                budget=PROBE_BUDGET_SECS_HINT,
            )
        )
        return 0
    finally:
        # N3 兜底：无论正常结束、返回还是抛异常/被 Ctrl-C，都把已完成的部分落盘。
        # 正常路径这里等同再写一次同样内容（几十 KB 顺序写），换来「中途被杀不白跑」。
        land_progress(args, pool, candidates, reports)


if __name__ == "__main__":
    raise SystemExit(main())
