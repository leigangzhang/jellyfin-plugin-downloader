"""`main()` 拆出来的收尾阶段：参数默认值、资源配置、结果交付。

阶段函数不直接抓 `search_pool` 自己的顶层名字，而是通过一个「按名字现取」的命名空间
访问（`Loaders`，与 probe_round 的同一套做法）：`search_pool.main()` 把自己模块的 globals
包起来传下来，所以 `mock.patch.object(search_pool, "<名字>")` 照样落到阶段函数里；
标准库与 media_download_lib 直接 import。

    prepare_args         --limit 越界提示与三个默认输出路径
    load_sources         资源站 / 关键字配置加载（含早退码 2 / 4）
    print_source_banner  本轮来源与预算的回显
    deliver_and_report   池落盘、候选清单、JSON 与结果回显
"""

import json
import os

import source_config

from media_download_lib import POOL_DIR


class Loaders:
    """把 search_pool 外观的全局命名空间按名字现取交给阶段函数。

    模块级 `from ... import` 只在导入时拷一次引用，补丁落不到阶段函数里；这里每次调用
    才取值。按名字取（不是按模块身份取），所以 `python3 search_pool.py`（外观是
    __main__）与 `import search_pool` 两种入口下都是同一份绑定。
    """

    __slots__ = ("_g",)

    def __init__(self, namespace):
        self._g = namespace

    def __getattr__(self, name):
        try:
            return self._g[name]
        except KeyError:
            raise AttributeError(name) from None


def prepare_args(ns, args):
    """限流提示与三个默认输出路径（collect 之前跑，语义与拆前的 main 开头一致）。"""
    if args.limit > ns.MAX_LIMIT:
        print(
            f"NOTE limit={args.limit} 超出 API 上限，已按 {ns.MAX_LIMIT} 发送"
            "（limit>10 会返回 status=400）"
        )
        args.limit = ns.MAX_LIMIT
    if not args.platform:
        args.platform = list(ns.DEFAULT_PLATFORMS)
    slug = ns.slugify(args.title)
    if not args.pool_file and not args.no_pool:
        args.pool_file = os.path.join(POOL_DIR, slug + ".json")
    if not args.candidates_out:
        args.candidates_out = f"/tmp/{slug}.magnets"
    if not args.json_out:
        args.json_out = f"/tmp/{slug}.search.json"


def load_sources():
    """读资源站 / 关键字配置；配置坏了就带早退码返回，不拿默认值兜底。"""
    sites, site_errors = source_config.load_sites()
    keywords, keyword_errors = source_config.load_keywords()
    for error in site_errors + keyword_errors:
        print("SOURCE_CONFIG_ERROR " + json.dumps({"error": error}, ensure_ascii=False))
    if not sites:
        # 配置坏了就明说，不拿默认值兜底——否则「我配了但没生效」是看不见的状态。
        print(
            "SOURCE_CONFIG_ERROR "
            + json.dumps(
                {"error": "没有可用的资源站，本轮不搜索（修好插件配置里的资源站列表后重试）"},
                ensure_ascii=False,
            )
        )
        return None, None, 2
    if not any(site.get("base_url") for site in sites):
        # 合法配置，只是没有磁力入口：只配了云盘专用站。说清楚，别让使用者以为搜索坏了。
        print(
            "NO_MAGNET_STATION "
            + json.dumps(
                {
                    "sites": [site["name"] for site in sites],
                    "reason": (
                        "配置的资源站都只有「网页搜索地址」、没有「接口地址」，"
                        "所以本轮没有磁力来源；云盘通道照常可用"
                        "（控制台选「仅云盘」或「磁力+云盘」）"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return None, None, 4
    return sites, keywords, 0


def print_source_banner(args, sites, keywords):
    """回显本轮来源与预算（stdout 文案与拆前逐字一致）。"""
    print(
        "搜索来源: "
        + json.dumps(
            {
                "sites": [
                    {"name": site["name"], "kind": site["kind"], "base_url": site["base_url"]}
                    for site in sites
                ],
                "l1_keywords": keywords,
                "limit": args.limit,
                "pages": args.pages,
                "page_budget_secs": args.page_budget_secs,
                "page_workers": args.page_workers,
                "max_queries": args.max_queries,
                "pool_file": args.pool_file or "disabled",
            },
            ensure_ascii=False,
        )
    )


def deliver_and_report(ns, args, pool, candidates, reports, external_reports, sites, keywords):
    """合并进池、写候选清单与 JSON、打印排名与 SEARCH_RESULT，返回退出码。"""
    usable = [
        item for item in candidates.values() if item.get("main_video_gb")
    ]
    no_video = [
        item for item in candidates.values() if not item.get("main_video_gb")
    ]
    merged = ns.merge_candidates_into_pool(pool, candidates)
    saved = ns.save_pool(args.pool_file, merged, args.title) if not args.no_pool else False
    ns.write_candidates_file(args.candidates_out, candidates, reports)
    episode_files = ns.write_episode_files(args.candidate_dir, candidates, args.season)

    payload = {
        "title": args.title,
        "alt_titles": args.alt_title,
        "year": args.year,
        "sites": [
            {"name": site["name"], "kind": site["kind"], "base_url": site["base_url"]}
            for site in sites
        ],
        "l1_keywords": list(keywords),
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
            budget=ns.PROBE_BUDGET_SECS_HINT,
        )
    )
    return 0
