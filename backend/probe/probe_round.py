"""probe 一轮的执行阶段：健康度、metadata、排名、交付。

`probe_magnets.main()` 只做编排：解析参数、起预算与计时器、依次调用这里的阶段函数，
阶段之间只通过返回值传状态。所有对「可打桩名字」（announce_health / tracker_health /
public_ip / fetch_metadata / _save_progress）的调用都走 `probe` 模块属性，
`mock.patch.object(probe_magnets, ...)` 才能照旧生效。
"""
import concurrent.futures
import json
import time

from candidate_score import (
    DEFAULT_WEIGHTS,
    format_candidate_line,
    normalize_policy,
    rank_candidates,
    score_band,
    structural_reject,
)
from media_download_lib import GB

from probe.probe_health import (
    ANNOUNCE_TIMEOUT_SECONDS,
    MIN_HEALTH_BUDGET_SECONDS,
    MIN_METADATA_BUDGET_SECONDS,
    MIN_NETWORK_BUDGET_SECONDS,
    SCRAPE_TIMEOUT_SECONDS,
)
from probe.probe_pool import is_fresh, pool_metadata, save_pool, update_pool_entry, write_json_atomic

# 健康度缓存 TTL（秒）：种子数变化快，1 小时过期。
HEALTH_TTL_SECONDS = 3600
# announce 的 external peer 不超过这个数就算「都是本机」，判死种。
DEAD_SWARM_PEER_CEILING = 3

# 「没有做种」的丢弃理由（写进快照 rejected）。字符串仍叫 dead_swarm_filtered 是为了
# 前端兼容：Web/inject_05_render.js 按这个字符串数「已过滤 N 条 0 种子候选」，
# 改字符串就要同时改 Web/（本任务范围外）。判据本身已按使用方 2026-10-15 的口径收紧。
NO_SEEDER_REASON = "dead_swarm_filtered"

# 「整轮都被滤空」的收尾码：与 no_eligible_phase 的 NO_ELIGIBLE_RESOURCE 同一个（4）。
NO_CANDIDATE_EXIT_CODE = 4


def has_no_seeder(item) -> bool:
    """磁力候选有没有做种：seeds 缺失 / None / 非数字 / <= 0 → True（该丢）。

    使用方口径（2026-10-15，真机佐证）：0 种子能用**只是概率问题**，好用且下载快的概率
    更低 —— 实测队长点过一条「种子 0/6」的候选，它确实过了元数据闸门并建了任务
    （迅雷 taskid 307），但随后速率只有几 KB/s、预计要一天以上。所以磁力侧一律按
    「有没有做种」过滤，**不再用 `is_dead_swarm` 这种可达性判决当门槛**（那只是
    「此刻能不能拿到 metadata」，与「值不值得下」是两件事）。

    云盘候选没有「做种」这个概念 → 永远不在这里被丢（`kind == "pan"` 直接放行）。
    """
    if not isinstance(item, dict):
        return False
    if item.get("kind") == "pan":
        return False
    seeds = item.get("seeds")
    if seeds is None:
        return True
    try:
        return float(seeds) <= 0
    except (TypeError, ValueError):
        return True


def drop_no_seeder(candidates):
    """拆成 (保留, 丢弃)；丢弃的候选不进交付列表，只把条数与理由留给上层。"""
    kept, dropped = [], []
    for item in candidates or []:
        (dropped if has_no_seeder(item) else kept).append(item)
    return kept, dropped


def build_policy(args, duration_seconds):
    weights = {
        name: getattr(args, f"weight_{name}") or DEFAULT_WEIGHTS[name]
        for name in DEFAULT_WEIGHTS
    }
    target_bitrate = args.target_bitrate_mbps
    if target_bitrate is None:
        target_bitrate = (args.bitrate_min_mbps + args.bitrate_max_mbps) / 2.0

    if args.kind == "episode":
        estimated_gb = duration_seconds * target_bitrate / 8.0 / 1024.0
        preferred_min = args.min_gb if args.min_gb is not None else 0.45 * estimated_gb
        target_size = (
            args.target_gb if args.target_gb is not None else estimated_gb
        )
        preferred_max = (
            args.preferred_max_gb
            if args.preferred_max_gb is not None
            else 1.9 * estimated_gb
        )
    else:
        preferred_min = args.min_gb if args.min_gb is not None else 5.0
        target_size = args.target_gb if args.target_gb is not None else 7.5
        preferred_max = (
            args.preferred_max_gb if args.preferred_max_gb is not None else 10.0
        )

    hard_max = args.max_gb
    if hard_max is None:
        hard_max = 15.0 if args.kind == "movie" else 10.0

    return normalize_policy(
        {
            "kind": args.kind,
            "duration_seconds": duration_seconds,
            "target_bitrate_mbps": target_bitrate,
            "target_size_gb": target_size,
            "preferred_min_gb": preferred_min,
            "preferred_max_gb": preferred_max,
            "hard_max_gb": hard_max,
            "weights": weights,
            "wanted_markers": args.wanted_marker,
        }
    )


def init_phase(args):
    """时长默认值 + 打分策略；返回 (duration_seconds, policy)。"""
    duration_seconds = args.duration_seconds
    if not duration_seconds:
        if args.duration_minutes:
            duration_seconds = args.duration_minutes * 60
        elif args.kind == "episode":
            duration_seconds = 50 * 60
        else:
            duration_seconds = 120 * 60

    policy = build_policy(args, duration_seconds)
    weights = policy["weights"]
    print(
        "打分权重: "
        + " | ".join(f"{name} {weights[name]:.2f}" for name in DEFAULT_WEIGHTS)
    )
    print(
        "策略: "
        + json.dumps(
            {
                "kind": policy["kind"],
                "target_bitrate_mbps": round(policy["target_bitrate_mbps"], 2),
                "target_size_gb": round(policy["target_size_gb"], 2),
                "preferred_gb": [
                    round(policy["preferred_min_gb"], 2),
                    round(policy["preferred_max_gb"], 2),
                ],
                "hard_max_gb": policy["hard_max_gb"],
                "min_score": args.min_score,
            },
            ensure_ascii=False,
        )
    )
    return duration_seconds, policy


def health_stage(args, budget, timers, pool, now, order, sources, completed, probe):
    """健康度阶段；返回 (resources, health_timed_out)。probe 见 probe_magnets.Loaders。"""
    resources = []
    # 本机公网 IP 只在真的要 announce 时才查（它是一次 ≤5s 的 HTTP 调用，
    # 而健康度全部命中缓存时根本用不到）。预算不够就不查，让 announce 自己去失败。
    own_ips_cache = []

    def own_ips():
        if not own_ips_cache:
            if budget.out_of_time("health", MIN_HEALTH_BUDGET_SECONDS):
                return []
            own_ips_cache.append(probe.public_ip())
        return own_ips_cache

    health_started = timers.start()
    health_timed_out = False
    for info_hash in order:
        entry = pool.get(info_hash) or {}
        seeds, peers = 0, 0
        health_method = entry.get("health_method") or ""
        external_peers = None
        if is_fresh(pool, info_hash, "health_at", HEALTH_TTL_SECONDS, now):
            seeds = int(entry.get("seeds") or 0)
            peers = int(entry.get("peers") or 0)
            external_peers = entry.get("external_peers")
            completed["health"] += 1
        elif budget.out_of_time("health", MIN_HEALTH_BUDGET_SECONDS):
            # 预算用尽：保留池里的旧值（可能为 0/None），不再发起 announce/scrape。
            health_timed_out = True
            seeds = int(entry.get("seeds") or 0)
            peers = int(entry.get("peers") or 0)
            external_peers = entry.get("external_peers")
            health_method = health_method or "cached"
        else:
            health = probe.announce_health(
                info_hash, own_ips(), timeout=budget.clamp_timeout(ANNOUNCE_TIMEOUT_SECONDS)
            )
            if health:
                # The tracker counts our own client too (once under the local
                # public IP, once under the VPN exit); those are not seeders.
                seeds = max(health["seeders"] - health["own_peers"], 0)
                peers = health["external_peers"]
                external_peers = health["external_peers"]
                health_method = "announce"
            elif budget.remaining() >= MIN_NETWORK_BUDGET_SECONDS:
                seeds, peers = probe.tracker_health(
                    info_hash, timeout=budget.clamp_timeout(SCRAPE_TIMEOUT_SECONDS)
                )
                health_method = "scrape"
            else:
                health_method = "budget-exhausted"
            completed["health"] += 1
            update_pool_entry(
                pool,
                info_hash,
                health_at=now,
                seeds=seeds,
                peers=peers,
                health_method=health_method,
                external_peers=external_peers,
            )
        resource = {
            "ih": info_hash,
            "sources": sources[info_hash],
            "seeds": seeds,
            "peers": peers,
            "health_method": health_method,
            "external_peers": external_peers,
            "dead_swarm": external_peers == 0,
        }
        # 外部索引快照（第二磁力源 torrents-csv）的种子数：probe 只负责把池里的
        # 事实带进候选字典，打分侧在 announce 没有实测时才拿它兜底（契约见
        # candidate_score._external_seeders_signal）。缺字段就**不写**这两个键：
        # 这正是「缺失 = 与没有外部信号时逐位相同」的那条契约。
        external_seeders = entry.get("external_seeders")
        if external_seeders is not None:
            resource["external_seeders"] = external_seeders
            external_age = entry.get("external_seeders_age_days")
            if external_age is not None:
                resource["external_seeders_age_days"] = external_age
        resources.append(resource)
        warning = (
            "  <- 无外部 seeder（tracker 只返回本机）"
            if resource["dead_swarm"]
            else ""
        )
        print(
            f"  {info_hash[:16]}... seeds={seeds:4d} peers={peers:4d}"
            f" [{health_method or 'cached'}]{warning}"
        )
    if health_timed_out:
        print("  健康度阶段软预算用尽：剩余候选沿用池里的旧健康度")
    timers.done("health", health_started)
    return resources, health_timed_out


def metadata_stage(args, budget, timers, pool, pool_path, now, resources, duration_seconds, policy, completed, probe):
    """metadata 阶段；返回 (enriched, rejected, skipped_uncached)。

    线程池里逐条 enrich，每条完成就落盘一次（_save_progress 走 probe 模块属性，
    测试的 patch 才拦得住）。
    """
    print(
        f"\nFetching metadata for {len(resources)} candidates "
        f"with {args.workers} workers..."
    )
    enriched = []
    rejected = []
    metadata_started = timers.start()
    metadata_deadline = budget.deadline if budget.enabled else None
    pending = []
    skipped_uncached = []
    for resource in resources:
        if budget.remaining() < MIN_METADATA_BUDGET_SECONDS:
            # 预算不够跑一次 metadata 抓取：已缓存的候选照常提交（不花网络时间，
            # enrich 只在需要联网时才看 deadline），未缓存的跳过并记账。
            if not pool_metadata(pool, resource["ih"], now):
                budget.out_of_time("metadata", MIN_METADATA_BUDGET_SECONDS)
                skipped_uncached.append(resource["ih"])
                continue
        pending.append(resource)
    if skipped_uncached:
        print(
            f"  软预算用尽：{len(skipped_uncached)} 个未缓存候选跳过 metadata 抓取"
            f"（timeout_stage={budget.timeout_stage}）"
        )
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(args.workers, 1)
    ) as executor:
        future_map = {
            executor.submit(
                probe.enrich,
                resource["ih"],
                args.timeout,
                duration_seconds,
                pool,
                now,
                args.refresh_metadata,
                metadata_deadline,
            ): resource
            for resource in pending
        }
        total = len(pending)
        for done, future in enumerate(
            concurrent.futures.as_completed(future_map), start=1
        ):
            resource = future_map[future]
            try:
                metadata = future.result()
            except Exception as exc:  # noqa: BLE001 - report, never crash the pool
                metadata = None
                resource["probe_error"] = str(exc)
            # 每条一落盘：这一轮的 metadata 是付过网络代价的，不能等到末尾。
            probe._save_progress(pool_path, pool, args.query)
            if not metadata:
                reason = "metadata unavailable (no seeder or tracker response)"
                probe_error = str(resource.get("probe_error") or "")
                rejected.append(
                    {
                        "ih": resource["ih"],
                        "name": "",
                        "reason": reason,
                        # F5：worker 里冒出来的真实异常不吞掉——reason 仍是分类用的
                        # 固定文案（退出码 7/8 的判定依赖它），异常另开一个字段。
                        "error": probe_error,
                    }
                )
                print(
                    f"  [{done}/{total}] SKIP {resource['ih'][:16]}... "
                    f"{reason}{(' :: ' + probe_error) if probe_error else ''}"
                )
                continue
            completed["metadata"] += 1
            resource.update(metadata)
            reason = structural_reject(resource, policy)
            if reason:
                rejected.append(
                    {
                        "ih": resource["ih"],
                        "name": resource.get("name", ""),
                        "reason": reason,
                    }
                )
                print(
                    f"  [{done}/{total}] SKIP "
                    f"{resource['main_video_gb']:6.2f}GB "
                    f"{str(resource.get('name'))[:50]} :: {reason}"
                )
                continue
            enriched.append(resource)
            print(
                f"  [{done}/{total}] KEEP "
                f"{resource['main_video_gb']:6.2f}GB "
                f"{str(resource.get('name'))[:60]}"
            )
    timers.done("metadata", metadata_started)
    return enriched, rejected, skipped_uncached


def merge_extras(enriched, extras):
    """没有磁力候选时，云盘候选独立成池；返回 (enriched, extras)。

    「仅对云盘候选打分」这句要等到云盘候选真的读进来了再说：两个文件都读不到时
    它若先打印，就会声称有云盘候选而实际零候选（复核第二轮 F7）。
    """
    if not enriched and extras:
        print(f"没有磁力候选，仅对 {len(extras)} 个云盘/非磁力候选打分")
        return extras, []

    return enriched, extras


def no_eligible_phase(rejected, resources, pool, pool_path, args, budget,
                      skipped_uncached, completed, round_started_at):
    """没有任何可交付候选：先落池，再按 partial / 死种 / 网络问题挑退出码。"""
    save_pool(pool_path, pool, args.query)
    # 这一轮没有产出可交付候选：池子已经落盘（partial 状态在里面），但
    # --json-out 保持不写 —— 控制台据此保留上一份快照，退出码语义不变。
    if budget.timeout_stage or skipped_uncached:
        print(
            "PROBE_PARTIAL "
            + json.dumps(
                {
                    "timeout_stage": budget.timeout_stage,
                    "expired_stages": budget.expired_stages,
                    "budget_secs": budget.total_seconds,
                    "skipped_uncached_metadata": len(skipped_uncached),
                    "elapsed_seconds": round(time.time() - round_started_at, 2),
                    "completed": completed,
                    "note": "预算用尽且没有可交付候选；池已落盘，未写 --json-out",
                },
                ensure_ascii=False,
            )
        )
    if rejected and all(
        item["reason"].startswith("metadata unavailable") for item in rejected
    ):
        external = [
            resource.get("external_peers")
            for resource in resources
            if resource.get("external_peers") is not None
        ]
        # A live swarm with more than a couple of reachable peers always
        # yields metadata within the timeout; a handful of "external"
        # peers that cannot deliver metadata is this machine talking to
        # itself (the tracker also caches our own announces).
        if external and max(external) <= DEAD_SWARM_PEER_CEILING:
            print(
                "SWARM_DEAD "
                + json.dumps(
                    {
                        "candidates": len(rejected),
                        "external_peers": external,
                        "reason": (
                            "no candidate could fetch metadata and the live "
                            "announce shows no external seeder: the torrent "
                            "is dead, not a network failure. Find another "
                            "source (cloud share, per-episode release)."
                        ),
                    },
                    ensure_ascii=False,
                )
            )
            return 8
        print(
            "METADATA_FAILED "
            + json.dumps(
                {
                    "candidates": len(rejected),
                    "reason": (
                        "no candidate produced torrent metadata; this is a "
                        "network/tracker problem, not a missing resource"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 7
    print("NO_ELIGIBLE_RESOURCE")
    return 4


def deliver(pool, pool_path, args, output, best):
    """把 best 落盘并决定退出码；返回 None 表示 best 已交付（调用方收尾打印）。"""
    save_pool(pool_path, pool, args.query)

    if best["score"] < args.min_score:
        output["best"] = None
        output["best_below_min_score"] = {
            "score": best["score"],
            "min_score": args.min_score,
            "ih": best.get("ih") or best.get("id"),
            "name": best.get("name", ""),
        }
        write_json_atomic(args.json_out, output)
        print(
            "BEST_BELOW_MIN_SCORE "
            + json.dumps(
                {
                    "score": best["score"],
                    "min_score": args.min_score,
                    "name": best.get("name", ""),
                    "kind": best.get("kind") or "magnet",
                    "magnet": output["ranked"][0]["magnet"],
                    "share_url": best.get("share_url", ""),
                },
                ensure_ascii=False,
            )
        )
        print("BEST_RESULT_JSON " + args.json_out)
        return 5

    if best.get("dead_swarm"):
        # 第 1 名已被 live-check / speed-probe 判定 metadata 不可达：即使
        # 分数看起来优秀（如 229 seeds 估算的 0.95 速度分），迅雷提交也
        # 必然「种子解析失败」。不把死种当 best 交付，而是明确降级信号。
        output["best"] = None
        output["best_dead_swarm"] = {
            "score": best["score"],
            "ih": best.get("ih") or best.get("id"),
            "name": best.get("name", ""),
            "magnet": output["ranked"][0]["magnet"],
            "reason": "metadata 不可达（dead-swarm），迅雷提交同样会种子解析失败",
            "next_step": "换一条候选或换资源站：在插件配置里增删资源站 / 关键字组合，"
                          "或给站点填上「网页搜索地址」走云盘通道",
        }
        write_json_atomic(args.json_out, output)
        print(
            "BEST_DEAD_SWARM "
            + json.dumps(
                {
                    "score": best["score"],
                    "name": best.get("name", ""),
                    "ih": best.get("ih") or best.get("id"),
                    "magnet": output["ranked"][0]["magnet"],
                    "next_step": "换一条候选或换资源站：在插件配置里增删资源站 / 关键字组合，"
                          "或给站点填上「网页搜索地址」走云盘通道",
                },
                ensure_ascii=False,
            )
        )
        print("BEST_RESULT_JSON " + args.json_out)
        return 8

    output["best"] = output["ranked"][0]
    output["magnet"] = output["ranked"][0]["magnet"]
    write_json_atomic(args.json_out, output)
    return None


def rank_phase(args, budget, timers, pool, now, enriched, extras, rejected, policy,
               completed, round_started_at, pool_path, skipped_uncached, probe):
    """打分、live-check 重排、打印排名；返回 (ranked, output, best)。probe 见 Loaders。"""
    enriched = enriched + extras

    ranked = rank_candidates(enriched, policy)

    live_summary = {"checked": 0, "reused_from_enrich": 0}
    if args.live_check and any(item.get("ih") for item in ranked):
        print("\n校验头部候选能否真正解析元数据（迅雷的“种子解析失败”就出在这一步）...")
        live_started = timers.start()
        live_summary = probe.verify_live(
            ranked, pool, now, args, round_started_at=round_started_at, budget=budget
        )
        timers.done("live_check", live_started)
        completed["live_check"] = live_summary["checked"]
        # re-score：live-check 把「本轮证明可达」写进了候选字典，排名要按新事实重算。
        ranked = rank_candidates(enriched, policy)

    timeout_stage = budget.timeout_stage
    partial = bool(timeout_stage or skipped_uncached)
    if partial:
        print(
            "PROBE_PARTIAL "
            + json.dumps(
                {
                    "timeout_stage": timeout_stage,
                    "expired_stages": budget.expired_stages,
                    "budget_secs": budget.total_seconds,
                    "skipped_uncached_metadata": len(skipped_uncached),
                    "elapsed_seconds": round(time.time() - round_started_at, 2),
                    "completed": completed,
                },
                ensure_ascii=False,
            )
        )

    # 没有做种的磁力候选不再交付（使用方 2026-10-15 口径）：0 种子即便有 peer，可用且快的
    # 概率很低，直接滤掉最有效。丢弃的候选不进 ranked，但在 rejected 里留一条 reason 记录，
    # 好让面板只报一行「已过滤 N 条 0 种子候选」（不列行、不给开关）。云盘候选原样保留。
    ranked, no_seeder_dropped = drop_no_seeder(ranked)
    if no_seeder_dropped:
        print(f"NO_SEEDER_FILTERED {len(no_seeder_dropped)}")
    for item in no_seeder_dropped:
        rejected.append(
            {
                "ih": item.get("ih") or item.get("id") or "",
                "name": item.get("name", ""),
                "reason": NO_SEEDER_REASON,
                "error": "",
            }
        )
    if not ranked:
        # 整轮候选都被滤空（例如全是 0 种子的磁力候选）：交空轮 + NO_CANDIDATES。
        # rank_phase 自己写好 probe JSON 并返回 best=None，调用方（probe_magnets.main）
        # 看到 best is None 就直接按 NO_CANDIDATE_EXIT_CODE 收尾，不进 deliver ——
        # deliver 依赖 ranked[0]，空名单进去只会崩。
        empty_output = {
            "query": args.query,
            "kind": args.kind,
            "partial": partial,
            "timeout_stage": timeout_stage,
            "expired_stages": budget.expired_stages,
            "budget_secs": budget.total_seconds,
            "completed": completed,
            "stage_seconds": timers.seconds,
            "live_check": live_summary,
            "policy": {key: value for key, value in policy.items() if key != "wanted_markers"},
            "min_score": args.min_score,
            "pool_file": pool_path,
            "ranked": [],
            "all_eligible": [],
            "rejected": rejected,
            "best": None,
            "no_seeder_filtered": len(no_seeder_dropped),
        }
        write_json_atomic(args.json_out, empty_output)
        print(
            "NO_CANDIDATES "
            + json.dumps(
                {
                    "no_seeder_filtered": len(no_seeder_dropped),
                    "reason": (
                        "every magnet candidate has no seeder (seeds missing or <= 0); "
                        "they are dropped before delivery and never stored"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return [], empty_output, None

    best = ranked[0]
    output = {
        "query": args.query,
        "kind": args.kind,
        "partial": partial,
        "timeout_stage": timeout_stage,
        "expired_stages": budget.expired_stages,
        "budget_secs": budget.total_seconds,
        "completed": completed,
        "stage_seconds": timers.seconds,
        "live_check": live_summary,
        "policy": {
            key: value
            for key, value in policy.items()
            if key != "wanted_markers"
        },
        "min_score": args.min_score,
        "pool_file": pool_path,
        "ranked": [
            {
                "rank": item.get("rank"),
                "ih": item.get("ih") or item.get("id"),
                "name": item.get("name", ""),
                "score": item.get("score"),
                "band": score_band(item.get("score", 0)),
                "dimensions": item.get("dimensions"),
                "breakdown": item.get("breakdown"),
                "seeds": item.get("seeds"),
                "peers": item.get("peers"),
                "main_video_gb": item.get("main_video_gb"),
                "estimated_bitrate_mbps": item.get("estimated_bitrate_mbps"),
                "resolution": item.get("resolution"),
                "source": item.get("source"),
                "codec": item.get("codec"),
                "has_chs": item.get("has_chs"),
                "hard_sub": item.get("hard_sub"),
                "release_group": item.get("release_group"),
                "sources": item.get("sources"),
                "kind": item.get("kind") or "magnet",
                "share_url": item.get("share_url", ""),
                "in_cloud": item.get("in_cloud"),
                "pre_transfer": item.get("pre_transfer"),
                "videos": item.get("videos") or [],
                "magnet": item.get("magnet")
                or (
                    f"magnet:?xt=urn:btih:{item['ih']}"
                    if item.get("ih")
                    else ""
                ),
            }
            for item in ranked
        ],
        "rejected": rejected,
        "best": None,
        "no_seeder_filtered": len(no_seeder_dropped),
    }
    output["all_eligible"] = output["ranked"]

    print("\n" + "=" * 104)
    print("排名 分数  [画质/速度/体验/适配]  种子/实测        分辨率   体积  字幕 名称")
    for index, item in enumerate(ranked, start=1):
        print(format_candidate_line(item, index))
    print("=" * 104)
    print(f"\n第 1 名得分 {best['score']:.2f}（{score_band(best['score'])}）")
    for name, value in (best.get("dimensions") or {}).items():
        print(f"  {name:<10} {value:6.2f}")
    return ranked, output, best
