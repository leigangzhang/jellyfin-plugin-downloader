"""取源、分页与预算：索引 API 与 torrents-csv 的候选构造，加上墙钟预算。

`fetch_query_pages` 是唯一的翻页实现（并发抓页、预算夹紧、无新 hash 停），
`Deadline` 是它和第二通道共用的墙钟预算，`build_query_plan` 产出关键词阶梯，
`torrent_to_candidate` / `torrents_csv_to_candidate` 把两边的条目折成同一个候选形状。
"""

import concurrent.futures
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import source_config
import torrents_csv_source

from media_download_lib import (
    GB,
    JUNK_EXTS,
    VIDEO_EXTS,
)
from search_pool_release import (
    CODE_HINT_RE,
)


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


# 结构化 API 站的缺省接口地址。真正用哪个站由配置决定（`collect` 对每个站显式传
# `base_url`）；这个缺省只服务于手工调用与测试，值取内置默认站。
DEFAULT_API_BASE_URL = source_config.DEFAULT_SITES[0]["base_url"]


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


def build_query_plan(
    title,
    alt_titles=(),
    year="",
    collection_hints=(),
    platforms=(),
    precision_query="",
    strict_match=False,
    anchor_keywords=(),
):
    """Precision-first query ladder; each entry is tried until the pool is big enough.

    Tier order (non-strict): **anchor** -> alias/year -> alias -> title/year ->
    title -> collection hints -> platform -> precision query. The anchor tier is
    `title + <configured keyword>` for every configured L1 keyword combination;
    it goes first because the publisher's own signature inside the release name
    is the most precise thing this index knows about a release. Aliases and the
    year come next because they are what buys precision on this index: a
    common-word title returned 0/10 intended hits while `The Knockout 2023`
    returned 10/10 (2026-10-13 audit). The bare title is still in the ladder --
    it is the only tier that works for titles with no alias and no year.

    Strict mode exists to keep a series search inside one season, so it never
    falls back to the bare title; it runs the anchor tier first and then only
    `<name> <hint>` combinations, with aliases ahead of the primary title (an
    alias is less ambiguous). The anchor tier carries no season marker, so under
    strict mode it can surface the show's other seasons -- there is no
    season-marker filter downstream, the caller's season terms are the only
    strictness today, and `title + <season hint> + <keyword>` measures 0 hits on
    this index (2026-10-13).
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

    # 发布者/站点署名档位：配置的每一条关键字组合都排在最前，对每个片名各跑一次。
    # 片名在前、别名在后（与下面的别名档相反）：索引里的名字是本地化片名，第一枪打它
    # 命中率最高。不做「中英不混搭」过滤——后缀是用户配的，成句与否由用户判断。
    for name in names:
        for keyword in anchor_keywords or ():
            add(f"{name} {keyword}", "anchor")

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


def search_api(query, limit=MAX_LIMIT, offset=0, base_url=DEFAULT_API_BASE_URL, timeout=20):
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
    already taken by the probe's extraction labels (`direct#N`), and overwriting
    it would corrupt that contract.

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
    base_url=DEFAULT_API_BASE_URL,
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
