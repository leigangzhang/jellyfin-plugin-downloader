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

Stations and the anchor tier
----------------------------
The stations are configuration, not constants: `source_config.py` owns the list
(`JMD_BT_SITES`), the `kind` -> adapter mapping, and the validation. Each station
gets an equal slice of the per-keyword wall clock, so **adding a station divides
the budget instead of extending the round**; the round's worst case stays
`--max-queries x --page-budget-secs` no matter how many stations are configured.

Before every other tier the plan asks each station for `title + keyword`, using
the configured L1 keyword combinations (`JMD_L1_KEYWORDS`, default
`高清剧集网` / `DDHDTV`). Those are the publisher's own signatures inside the
release name (`【高清剧集网发布 www.DDHDTV.com】...`), which makes this the most
precise tier the index understands. Measured 2026-10-13 on `m.diao.im`:
`漫长的季节 DDHDTV` -> 6/6 publisher hits, `漫长的季节 高清剧集网` -> 10/10, but
`漫长的季节 高清剧集发布网` -> 0 and `漫长的季节 BBHDTV` -> 0 -- the defaults are
the two that were measured to work, not the phrase that reads like the
publisher's name. The tier also runs under `--strict-match`: it carries no
season marker, so a strict season search mixes the show's other seasons into the
list (the strictly-seasonal tiers still run, after it).

Keyword policy
--------------
Aliases/original titles (with the year) come next, then the bare title, then
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
  label space (`direct#N`).

拆分说明（按职责拆模块，纯重构）
--------------------------------
本文件的模块体里没有任何兄弟模块的 import（tests/test_backend_sources_dependencies.py 的
DependencyTests 把 search_pool.py 的 import 根限定为既有那几个模块名 + 标准库），
公共名字由承载侧 `search_pool_parts` 显式 import，本文件再「真实赋值」进自己的 globals，
并由模块级 `__getattr__` 兜底转发（PEP 562）。为什么必须真实赋值而不是只转发：见下面
那段循环的注释——`mock.patch.object(search_pool, "fetch_query_pages")`
/ `"land_progress"` 打的就是本模块的全局名。

    search_pool_parts     承载侧：把拆出的公共名字显式 import 收拢在一起
    search_pool_round     收尾阶段：参数默认值、资源配置、结果交付
    search_pool_release   发行名 / 季号识别
    search_pool_api       取源、分页与预算
    search_pool_pool      池条目合并与外部源
    search_pool_deliver   候选清单 / 进度落盘

collect() / parse_args() / main() 仍住在本文件：collect 与 main 的裸名字必须走本模块
globals，打桩语义才与拆前一致。
"""

import os as _bootstrap_os
import sys as _bootstrap_sys

# 本脚本被 console_server 以 `python3 backend/<pkg>/xxx.py` 拉起时，sys.path[0] 是这个
# 子目录；包内绝对 import（from probe import ...）与根目录模块（media_download_lib /
# source_config）都要求 backend/ 在 sys.path 上，所以先把自己所在包的根补进去。
_BOOTSTRAP_ROOT = _bootstrap_os.path.dirname(
    _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
)
if _BOOTSTRAP_ROOT not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_ROOT)

import argparse
import importlib
import json
import time
import urllib.error  # noqa: F401 - 兼容面：测试用 search_pool.urllib.error.HTTPError
import urllib.parse  # noqa: F401 - 兼容面：与拆前一致，urllib.parse 仍挂在 search_pool 名下
import urllib.request  # noqa: F401 - 兼容面：测试用 mock.patch.object(search_pool.urllib.request, "urlopen")

import source_config
import torrents_csv_source

# 承载侧模块名。这里只能是字符串：写 import 语句会让 DependencyTests 那条
# 「只允许既有模块名 + 标准库」的守卫变红（它扫的是 Import/ImportFrom 节点）。
_PARTS_MODULE = "search.search_pool_parts"


def __getattr__(name):
    """模块级属性转发（PEP 562）：本模块没有的属性现取承载侧的同名属性。

    显式赋值过的名字（下面那段循环 + 本文件自己的 collect/parse_args/main）不走这里；
    这条兜底保证 `search_pool.<承载侧的公共名>` 一律可读——`build_console.py` 的
    `search_pool.candidate_seasons(...)` 与 `inspect.signature(search_pool.search_api)`
    都是属性读取，打桩时 mock 也是先 getattr 取原值。
    """
    return getattr(importlib.import_module(_PARTS_MODULE), name)


# 把承载侧的公共名字绑进本模块 globals。
#
# 必须是真实赋值，不能只留 __getattr__ 转发：collect() / main() 里的裸名字走 LOAD_GLOBAL
# 查本模块 globals（**不查**模块 __getattr__），而 tests/test_backend_search_budget.py
# 正是用 `mock.patch.object(sp, "fetch_query_pages")` 与 `mock.patch.object(sp, "land_progress")`
# 打这两个调用点。赋值进来的是模块字典里的真实条目，mock 的 save/restore 因此与拆前一致
# （拆前这些名字本来就是本模块的全局名）。
_parts = importlib.import_module(_PARTS_MODULE)
for _name, _value in vars(_parts).items():
    if not _name.startswith("_"):
        globals()[_name] = _value
del _name, _value, _parts


def collect(args, sites=None, keywords=None):
    """Run the query ladder once per configured station.

    返回 (pool, candidates, reports, external_reports)。`sites` / `keywords` 省略时
    从环境变量读（`source_config`）；显式传入是留给测试的入口。

    `--publisher-only` 时阶梯只剩锚点档：这是「只用这一家源」的语义，不是「优先用它」
    ——优先靠打分侧的偏好，单源靠这里收窄搜索面。
    """
    pool = load_pool(args.pool_file)
    candidates = {}
    reports = []
    external_reports = []
    now = time.time()

    if sites is None:
        sites = source_config.load_sites()[0]
    if keywords is None:
        keywords = source_config.load_keywords()[0]
    # 只有带接口地址的站才取磁力；只填网页搜索地址的云盘专用站两个都不是
    # （见 source_config.magnet_sites）。
    api_sites, extra_sites = source_config.magnet_sites(sites)

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
        anchor_keywords=keywords,
    )
    if getattr(args, "publisher_only", False):
        # 锚点档就是「这一家源」的档位，单源模式把其它档位全部丢掉。档位来自配置，
        # 与片名用哪种字符无关，所以不再需要「片名不含 ASCII 就补一条查询」的兜底。
        plan = [step for step in plan if step.get("tier") == "anchor"]
    if args.max_queries:
        plan = plan[: args.max_queries]
    request_limit = max(1, min(int(args.limit), MAX_LIMIT))

    total_budget = float(getattr(args, "page_budget_secs", DEFAULT_PAGE_BUDGET_SECS))
    reserve = 0.0
    if extra_sites:
        requested = float(getattr(args, "extra_source_reserve_secs", 0.0) or 0.0)
        reserve = requested if requested > 0 else min(
            float(getattr(args, "extra_source_timeout", DEFAULT_EXTRA_SOURCE_RESERVE_SECS)),
            DEFAULT_EXTRA_SOURCE_RESERVE_SECS,
        )
        # 预留切片不能吃掉整个预算，否则翻页永远没得跑。
        reserve = min(reserve, max(total_budget - 1.0, 0.0))
    # 预算按站均分：**加一个站是切分同一份墙钟，不是把这一轮拉长**。每个（档位, 站）
    # 组合拿自己的一份切片，所以最坏墙钟仍是 `--max-queries × --page-budget-secs`，
    # 与配了几个站无关——这是控制台 `L1_MAX_QUERIES × L1_KEYWORD_BUDGET_SECS ≤ 450s`
    # 那条推算成立的前提（见 console_server 顶部注释）。
    api_budget = max(total_budget - reserve, 1.0) / max(len(api_sites), 1)
    extra_budget = reserve / len(extra_sites) if extra_sites else 0.0
    # 每个站一份「见过的 hash」：它同时是「连续两页没有新 hash 就停」的判据
    # （见 fetch_query_pages），所以两个索引不能共用一份——第二个站在第一个站已经
    # 返回过的那批 hash 上会被误判成「没有新东西」而提前停止翻页。候选去重仍然是全局
    # 的（`candidates` 以 info hash 为键）。
    seen_hashes_by_site = {}

    if not api_sites:
        # 只配了非 API 站时，阶梯一次都不用跑：没有站会去查它，跑它只会多打印几行、
        # 多落几次盘。非 API 站自己的循环在下面。
        plan = []

    # 严格按季的收口放在**落盘路径上**，不是最后一步：`land_progress` 每完成一个关键词
    # 就写一次盘，被杀（控制台 600s 硬杀、rc=124）时那份文件照样会被交出去。只有在每次
    # 落盘前收口，「中途被杀的那一轮」也已经是收口后的结果（复核第二轮 F2）。
    strict_season = (
        int(getattr(args, "season", 0) or 0)
        if getattr(args, "strict_match", False) else 0
    )
    season_dropped = []

    def drop_other_seasons():
        nonlocal candidates
        if not strict_season:
            return
        candidates, dropped = strict_season_filter(candidates, strict_season)
        season_dropped.extend(dropped)

    try:
        for step in plan:
            query = step["query"]
            for site in api_sites:
                site_name = site.get("name") or site.get("kind") or "site"
                seen_hashes = seen_hashes_by_site.setdefault(site["base_url"], set())
                deadline = Deadline(api_budget)
                outcome = fetch_query_pages(
                    query,
                    request_limit=request_limit,
                    pages=getattr(args, "pages", DEFAULT_PAGES),
                    budget=deadline,
                    base_url=site["base_url"],
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
                        dict(error, query=query, site=site_name), ensure_ascii=False))

                reports.append(
                    {
                        "query": query,
                        "tier": step["tier"],
                        "site": site_name,
                        "pages": len(outcome["pages"]),
                        "requests": outcome["requests"],
                        "hits": hits,
                        "new": fresh,
                        "has_more": outcome["pages"][-1]["has_more"] if outcome["pages"] else False,
                        "stopped_by": outcome["stopped_by"],
                        "elapsed_secs": round(deadline.elapsed(), 2),
                    }
                )
                print(
                    f"  [{step['tier']:<9}] {site_name[:14]:<14} {query[:38]:<38} "
                    f"hits={hits:3d} new={fresh:3d} "
                    f"页={len(outcome['pages'])} {outcome['stopped_by']} "
                    f"{deadline.elapsed():.1f}s"
                )
            # N3：每完成一个关键词（含它在每个站上的那次查询）就把已完成的部分原子
            # 落盘（池 + 候选磁力）。落盘前先做严格按季的收口。
            drop_other_seasons()
            land_progress(args, pool, candidates, reports, now=now)
            usable = [
                item for item in candidates.values() if item.get("main_video_gb")
            ]
            print(f"  [{step['tier']:<9}] 本档累计可用={len(usable)}")
            if len(usable) >= args.enough:
                print(f"  已达到 --enough {args.enough}，停止扩展关键词")
                break
            if args.sleep:
                time.sleep(args.sleep)

        drop_other_seasons()
        for site in extra_sites:
            names = [name for name in [args.title, *(args.alt_title or [])] if name]
            external_reports.append(
                collect_external_source(
                    args, site, names, candidates, Deadline(extra_budget)
                )
            )
        for report in external_reports:
            print("EXTRA_SOURCE " + json.dumps(report, ensure_ascii=False))
        if season_dropped:
            print(
                "STRICT_SEASON_FILTER "
                + json.dumps(
                    {
                        "season": strict_season,
                        "kept": len(candidates),
                        "dropped": len(season_dropped),
                        "examples": season_dropped[:5],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        # 手动中断（KeyboardInterrupt）/异常路径也兜一次：pager 已经返回的那几个
        # 关键词的候选不能因为后面被杀而丢掉。正常路径这里等同再写一次同样内容。
        land_progress(args, pool, candidates, reports, now=now)

    stamp_candidates(candidates, now)
    return pool, candidates, reports, external_reports


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
        "--publisher-only",
        action="store_true",
        help=(
            "只搜索被偏好的发布者（片名 + DDHDTV 档位），并只保留该源候选；"
            "用于「只要这一家源」的场景"
        ),
    )
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
    parser.add_argument(
        "--extra-source-limit",
        type=int,
        default=torrents_csv_source.DEFAULT_RESULT_LIMIT,
        help="非 API 站（如 torrents-csv）每次查询要几条",
    )
    parser.add_argument(
        "--extra-source-timeout",
        type=int,
        default=torrents_csv_source.DEFAULT_TIMEOUT,
        help="非 API 站单次查询超时（同时是默认的预留切片秒数，见下）",
    )
    parser.add_argument(
        "--extra-source-reserve-secs",
        type=float,
        default=DEFAULT_EXTRA_SOURCE_RESERVE_SECS,
        help="从 --page-budget-secs 里给非 API 站预留的总秒数，按站均分",
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


def main():
    """一轮取源：读参数 → 读配置 → collect → 收口 → 交付。"""
    ns = Loaders(globals())
    args = ns.parse_args()
    prepare_args(ns, args)
    sites, keywords, code = load_sources()
    if code:
        return code
    print_source_banner(args, sites, keywords)
    # collect() 自己带 try/finally（每完成一个关键词落一次盘），所以这里只在它
    # **成功返回之后**再包一层：collect 抛异常时不能进这个 finally——那时局部名
    # 还没绑定，用空字典落一次盘会把刚才的渐进落盘成果清成空文件（实测过）。
    pool, candidates, reports, external_reports = ns.collect(
        args, sites=sites, keywords=keywords
    )
    try:

        if args.collapse_releases:
            collapsed, dropped = ns.collapse_release_duplicates(candidates)
            print("COLLAPSE_RELEASES " + json.dumps(
                {"groups": len(collapsed), "dropped": sum(len(v) for v in dropped.values())},
                ensure_ascii=False))
            candidates = collapsed

        if getattr(args, "publisher_only", False):
            # 单源：只留配置里那家发布者的候选。候选在收集阶段已经只剩锚点档返回的
            # 内容，这里再按名字收一次口，防止索引把不相关结果混进同一个查询里。
            # 判据同时取「配置的关键字」与「种子名里的静态署名」：两者可能不是同一个
            # 字符串（`高清剧集发布网` 查得到 0 条，而名字里写的是 `高清剧集网`）。
            tokens = ns.publisher_filter_tokens(keywords)
            kept = {
                ih: item
                for ih, item in candidates.items()
                if ns.is_publisher_candidate(item.get("name"), tokens)
            }
            dropped = len(candidates) - len(kept)
            print(
                "PUBLISHER_ONLY "
                + json.dumps(
                    {"kept": len(kept), "dropped": dropped, "tokens": list(tokens)},
                    ensure_ascii=False,
                )
            )
            if not kept:
                # 单源模式下「一条都没有」必须说清是哪来的空：这些关键词没有这个片名
                # 的资源，而不是搜索坏了或索引挂了。否则使用者只会看到一个空结果。
                print(
                    "PUBLISHER_ONLY_EMPTY "
                    + json.dumps(
                        {
                            "keywords": list(keywords),
                            "dropped": dropped,
                            "reason": (
                                "配置的 L1 关键字没有这个片名的资源；"
                                "在插件配置里增删关键字组合，或关掉单源模式回到全源搜索"
                            ),
                        },
                        ensure_ascii=False,
                    )
                )
            candidates = kept
        return deliver_and_report(
            ns, args, pool, candidates, reports, external_reports, sites, keywords
        )
    finally:
        # N3 兜底：无论正常结束、返回还是抛异常/被 Ctrl-C，都把已完成的部分落盘。
        # 正常路径这里等同再写一次同样内容（几十 KB 顺序写），换来「中途被杀不白跑」。
        ns.land_progress(args, pool, candidates, reports)


if __name__ == "__main__":
    raise SystemExit(main())
