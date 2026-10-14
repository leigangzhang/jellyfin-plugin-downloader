#!/usr/bin/env python3
"""Build a candidate pool and rank it with one weighted score.

Flow:

    gather candidates            --candidate-file from search_pool / --extra-candidates
      -> tracker health          seeds and peers per info hash
      -> metadata                file list, size, resolution, bitrate (a reference
                                 value for the score, plus the structural gate)
      -> score                   candidate_score.rank_candidates
      -> re-score and report     live-check writes back, ranked table is rebuilt
      -> rank                    best candidate + full ranked table

Nothing except structural validity and the disk-safety ceiling is filtered
out; the skill's old core rules (1080p, 4-6 Mbps, 5-10 GB, Chinese subtitles)
are weighted preferences inside the score.

The aria2c speed probe is gone. It measured 1.16-8.50x spread across repeats of
the same torrent and cost 17.6-24.1s per candidate, so it could not order the
list; the "speed" dimension is now the seed/peer prior plus the external-seeder
snapshot, with no measured-throughput branch.

实现按职责拆在同一个目录下的几个模块里，这个文件只做两件事：

1. 编排：parse_args() 解析 CLI，main() 依次调用 probe_round 的阶段函数；
2. 向后兼容：下面那一组 import 把拆分前的顶层名字重新绑回本模块。测试与
   调用方仍然可以 `mock.patch.object(probe_magnets, "fetch_metadata")`，或直接
   引用 probe_magnets.SoftBudget 之类；`main()` 里的阶段调用都走 probe_round，
   而 probe_round 只通过 `probe.<name>` 找这几个可打桩名字，所以补丁照样生效。

    probe_health      tracker / announce / DHT 网络层 + 预算/计时器
    probe_candidate   候选清单读取与结构过滤
    probe_pool        候选池磁盘格式与 metadata 缓存判定
    probe_metadata    aria2c metadata 抓取与 enrich
    probe_round       健康度 / metadata / 排名 / 交付四个阶段
"""

import argparse
import json
import subprocess  # noqa: F401 - 测试用 mock.patch.object(probe_magnets, "subprocess", ...)
import time

from candidate_score import DEFAULT_MIN_SCORE, DEFAULT_WEIGHTS, score_band
from media_download_lib import GB

from probe_candidate import (
    decode_thunder_link,
    dedupe,
    gather_candidates,
    load_extra_candidates,
    parse_candidate,
)
from probe_health import (
    ANNOUNCE_TIMEOUT_SECONDS,
    HTTP_TRACKERS,
    ANNOUNCE_TRACKERS,
    MIN_HEALTH_BUDGET_SECONDS,
    MIN_METADATA_BUDGET_SECONDS,
    MIN_NETWORK_BUDGET_SECONDS,
    SCRAPE_TIMEOUT_SECONDS,
    SoftBudget,
    StageTimers,
    announce_health,
    bdecode,
    parse_magnet,
    public_ip,
    scrape_tracker,
    tracker_health,
)
from probe_metadata import (
    ARIA2_DHT_FLAG,
    MIN_VIDEO_BYTES,
    fetch_metadata,
    parse_show_files,
)
from probe_pool import (
    HEALTH_TTL_SECONDS,
    METADATA_TTL_SECONDS,
    _POOL_LOCK,
    _save_progress,
    is_fresh,
    load_pool,
    metadata_has_usable_video,
    pool_file_for,
    pool_metadata,
    save_pool,
    update_pool_entry,
    write_json_atomic,
)


class Loaders:
    """把「可打桩名字」从主模块的命名空间交给阶段函数，调用时才取值。

    拆分后阶段函数住在 probe_round 里，而测试（以及调用方）习惯
    `mock.patch.object(probe_magnets, "fetch_metadata")`。模块级 `from ... import`
    只会在导入时拷贝一次引用，补丁就落不到阶段函数里；所以 main() 把自己所在
    模块的 globals() 包成这个只读视图传下去，阶段函数每次调用现取。

    直接 `python3 backend/probe_magnets.py` 运行时本模块叫 __main__，probe_round
    再 `import probe_magnets` 会加载出第二个模块对象；这个类是按名字取、不是按
    模块身份取，所以两种入口下都是同一份绑定。
    """

    __slots__ = ("_g",)

    def __init__(self, namespace):
        self._g = namespace

    def __getattr__(self, name):
        try:
            return self._g[name]
        except KeyError:
            raise AttributeError(name) from None


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument(
        "--candidate-file",
        help="search_pool 写下的候选清单：每行一个磁力或 info hash",
    )
    parser.add_argument("--timeout", type=int, default=25, help="Metadata timeout")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--kind", choices=["movie", "episode"], default="movie")
    parser.add_argument(
        "--min-gb",
        type=float,
        default=None,
        help="Preferred lower size bound (soft); default 5GB movie",
    )
    parser.add_argument(
        "--max-gb",
        type=float,
        default=None,
        help="Disk-safety ceiling; a single video above this is rejected",
    )
    parser.add_argument(
        "--target-gb",
        type=float,
        default=None,
        help="Preferred size (soft); default 7.5GB movie",
    )
    parser.add_argument("--preferred-max-gb", type=float, default=None)
    parser.add_argument("--duration-minutes", type=float)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument(
        "--target-bitrate-mbps",
        type=float,
        default=None,
        help="Preferred bitrate at 1080p; scaled by pixel count for other tiers",
    )
    parser.add_argument("--bitrate-min-mbps", type=float, default=4.0)
    parser.add_argument("--bitrate-max-mbps", type=float, default=6.0)
    parser.add_argument(
        "--allow-bitrate-out-of-range",
        action="store_true",
        help="Deprecated no-op: bitrate is always scored, never gated",
    )
    parser.add_argument(
        "--allow-other-resolution",
        action="store_true",
        help="Deprecated no-op: resolution is always scored, never gated",
    )
    for name in DEFAULT_WEIGHTS:
        parser.add_argument(f"--weight-{name}", type=float, default=None)
    parser.add_argument(
        "--wanted-marker",
        action="append",
        default=[],
        help="Episode markers this candidate should cover, e.g. S01E01",
    )
    parser.add_argument(
        "--no-live-check",
        dest="live_check",
        action="store_false",
        help="Skip the live metadata fetch that proves a swarm still exists",
    )
    parser.add_argument(
        "--live-check-top",
        type=int,
        default=3,
        help="How many leading candidates to verify against the live swarm",
    )
    parser.add_argument("--live-check-ttl", type=int, default=3600)
    parser.add_argument(
        "--budget-secs",
        type=float,
        default=0.0,
        help=(
            "Soft wall-clock budget for the whole probe round (0 = unlimited). "
            "Once exceeded, stages stop starting new network work, cached "
            "candidates still finish, and the pool + JSON are landed with "
            "partial/timeout_stage set."
        ),
    )
    parser.add_argument("--pool-file", default="")
    parser.add_argument(
        "--extra-candidates",
        default="",
        help="JSON list from pan_pool.py (云盘候选) merged into the same ranking",
    )
    parser.add_argument("--no-pool", action="store_true")
    parser.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="Ignore cached torrent metadata and re-probe",
    )
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--json-out", default="/tmp/candidate_pool.json")
    return parser.parse_args()


def enrich(info_hash, timeout, duration_seconds, pool, now, refresh_meta, deadline=None):
    """Metadata for one candidate: pool cache first, network only if allowed.

    A cache hit costs nothing and always goes through, even past the budget —
    that is what keeps the partial result useful. Only the network fetch is
    gated, and it receives the budget deadline so aria2c cannot outlive it.

    抓到 metadata 后只在 `metadata_has_usable_video()` 成立时写 `metadata_at`
    （字段契约见该函数）；清单里没有可用视频的候选照写其余字段，但不盖缓存戳，
    于是下一轮会重抓而不是被缓存成「已核实」。
    """
    metadata = None if refresh_meta else pool_metadata(pool, info_hash, now)
    if metadata is None:
        if deadline is not None and time.monotonic() >= deadline:
            return None
        metadata = fetch_metadata(info_hash, timeout, deadline=deadline)
        if metadata:
            fields = dict(metadata)
            if metadata_has_usable_video(metadata):
                fields["metadata_at"] = now
            update_pool_entry(pool, info_hash, **fields)
    if not metadata:
        return None
    enriched = dict(metadata)
    if duration_seconds and enriched.get("main_video_gb"):
        enriched["estimated_bitrate_mbps"] = (
            enriched["main_video_gb"] * GB * 8 / duration_seconds / 1e6
        )
    else:
        enriched["estimated_bitrate_mbps"] = None
    return enriched


def verify_live(ranked, pool, now, args, round_started_at=0.0, budget=None):
    """Prove the leaders can actually produce torrent metadata right now.

    Metadata that came from a search index says nothing about whether the
    swarm still exists: a pack with index metadata and no seeders scores well
    and then fails in Thunder with "种子解析失败". Fetching metadata from the
    swarm is the only honest check, so the leaders get one before submission.

    但**同一轮里刚抓到的 metadata 就是这次 live 证明**：enrich 阶段已经从
    swarm 取到并写了 `metadata_at=now`，再抓一次只是把同样的网络代价付第二遍
    （T1 实测头部候选每轮至少 2 次 aria2c metadata 抓取）。所以只要
    `metadata_at >= round_started_at`，就直接认定可达并写 `probe_ok_at`，
    不再调用 fetch_metadata。

    两个时间戳是两件事，定义只此一处（与 `search_pool.py` 的字段契约一致）：
    `metadata_at` = **这份候选的文件清单已核实（且含可用视频）**；
    `probe_ok_at` = **本轮证明过这个 hash 当前能产出 metadata**。写入规则见
    `metadata_has_usable_video()`：清单里没有可用视频时不写 `metadata_at`
    （所以「裸名字」的候选不会被缓存成已核实，也不会在这里被当作 live 证明）。

    `budget`（SoftBudget）用尽时不再发起新的 metadata 抓取。
    """
    checked = 0
    reused = 0
    for candidate in ranked:
        if checked >= max(args.live_check_top, 1):
            break
        if candidate.get("kind") == "pan":
            continue
        info_hash = candidate.get("ih")
        if not info_hash:
            continue
        if is_fresh(pool, info_hash, "probe_ok_at", args.live_check_ttl, now):
            candidate["metadata_verified"] = True
            continue
        fetched_this_round = is_fresh(
            pool, info_hash, "metadata_at", max(now - round_started_at, 0), now
        ) if round_started_at else False
        if fetched_this_round:
            # 本轮 enrich 刚证明过可达，不再重复抓取。
            candidate["metadata_verified"] = True
            candidate["dead_swarm"] = False
            reused += 1
            update_pool_entry(pool, info_hash, probe_ok_at=now)
            continue
        if budget is not None and budget.out_of_time("live-check", MIN_METADATA_BUDGET_SECONDS):
            print(
                f"  软预算已用尽，跳过剩余 live-check"
                f"（timeout_stage={budget.timeout_stage}）"
            )
            break
        checked += 1
        deadline = budget.deadline if budget is not None and budget.enabled else None
        metadata = fetch_metadata(info_hash, args.timeout, deadline=deadline)
        if metadata:
            candidate["metadata_verified"] = True
            candidate["dead_swarm"] = False
            fields = {"probe_ok_at": now}
            if metadata_has_usable_video(metadata):
                # 只有「清单里有可用视频」才盖章；否则下一轮应重抓（N7 单点定义）。
                fields["metadata_at"] = now
            update_pool_entry(pool, info_hash, **fields)
            print(f"  实测可解析元数据: {str(candidate.get('name'))[:56]}")
        else:
            candidate["metadata_verified"] = False
            external = candidate.get("external_peers")
            if external:
                # announce 仍能看到外部 peer：本次拿不到元数据可能只是超时/抖动，
                # 不足以判定死种（实测这类种子在迅雷里能正常下载）。
                candidate["dead_swarm"] = False
                # `probe_failed_at` / 池里的 `dead_swarm` 都只写不读：打分侧按本轮的
                # `external_peers` 重算可达性，这里写的是**留档**（事后看池子能知道
                # 这一轮为什么没交付），不是给某个读侧吃的。别把它们当死代码删掉。
                update_pool_entry(pool, info_hash, probe_failed_at=now, dead_swarm=False)
                print(
                    f"  本次未解析出元数据，但 announce 仍有 {external} 个外部 peer"
                    f" → 不判死种: {str(candidate.get('name'))[:48]}"
                )
            else:
                candidate["dead_swarm"] = True
                update_pool_entry(pool, info_hash, probe_failed_at=now, dead_swarm=True)
                print(
                    f"  无法解析元数据（迅雷同样会失败）: "
                    f"{str(candidate.get('name'))[:56]}"
                )
    return {"checked": checked, "reused_from_enrich": reused}


def main():
    args = parse_args()
    round_started_at = time.time()
    budget = SoftBudget(args.budget_secs)
    timers = StageTimers()
    completed = {"health": 0, "metadata": 0, "live_check": 0}
    if budget.enabled:
        print(
            "软预算: "
            + json.dumps(
                {
                    "budget_secs": budget.total_seconds,
                    "note": "超出后各阶段不再发起新网络调用；已缓存的候选照常完成",
                },
                ensure_ascii=False,
            )
        )

    duration_seconds, policy = round_.init_phase(args)
    probe = Loaders(globals())

    extracted = gather_candidates(args)
    if extracted is None:
        # 候选文件读不到（路径写错、或本轮控制台根本没写出它）。若还给了云盘候选，
        # 就不该整轮退出：**云盘候选是独立的一条通道**，磁力侧读不到文件不该把它一起丢掉
        # （rc=2 会让面板什么都看不到，且 --extra-candidates 连读都没读到）。
        if not args.extra_candidates:
            return 2
        print("CANDIDATE_FILE_MISSING 继续对 --extra-candidates 打分")
        extracted = []
    if not extracted:
        if not args.extra_candidates:
            print(
                "NO_CANDIDATES 需要 --candidate-file（search_pool 的产物）"
                "或 --extra-candidates（pan_pool 的云盘候选）"
            )
            return 4

    order, sources = dedupe(extracted)
    now = time.time()
    pool_path = pool_file_for(args)
    pool = load_pool(pool_path)
    print(
        f"候选池: {len(order)} 个 info hash "
        f"(新增 {len([h for h in order if h not in pool])}) "
        f"pool={pool_path or 'disabled'}"
    )

    resources, _health_timed_out = round_.health_stage(args, budget, timers, pool, now, order, sources, completed, probe)

    enriched, rejected, skipped_uncached = round_.metadata_stage(
        args, budget, timers, pool, pool_path, now, resources, duration_seconds, policy,
        completed, probe
    )

    extras = load_extra_candidates(args.extra_candidates, policy)
    enriched, extras = round_.merge_extras(enriched, extras)

    if not enriched:
        return round_.no_eligible_phase(
            rejected, resources, pool, pool_path, args, budget,
            skipped_uncached, completed, round_started_at,
        )

    ranked, output, best = round_.rank_phase(args, budget, timers, pool, now, enriched, extras, rejected, policy,
        completed, round_started_at, pool_path, skipped_uncached, probe)

    exit_code = round_.deliver(pool, pool_path, args, output, best)
    if exit_code is not None:
        return exit_code

    print("\nBEST_RESULT_JSON " + args.json_out)
    print(
        "Best: score={:.2f} {} GB={:.2f} bitrate={:.2f}Mbps seeds={} res={} CHS={}".format(
            best["score"],
            score_band(best["score"]),
            float(best.get("main_video_gb") or 0),
            float(best.get("estimated_bitrate_mbps") or 0),
            best.get("seeds", 0),
            best.get("resolution"),
            best.get("has_chs"),
        )
    )
    if output["magnet"]:
        print(f"Magnet: {output['magnet']}")
    else:
        print(
            "CLOUD_RESOURCE "
            + json.dumps(
                {
                    "name": best.get("name", ""),
                    "share_url": best.get("share_url", ""),
                    "in_cloud": best.get("in_cloud"),
                    "pre_transfer": best.get("pre_transfer"),
                    "note": "先转存到云盘再取回本地，见 references/operations.md",
                },
                ensure_ascii=False,
            )
        )
    return 0


# 注意：`import probe_round` 必须放在文件末尾。probe_round 反过来要
# `import probe_magnets as probe` 才能让 mock.patch.object(probe_magnets, ...) 生效，
# 所以在文件开头 import 会撞上「probe_magnets 还没加载完」的死结；这里延迟到
# 本模块全部定义就绪之后再绑。
import probe_round as round_
from probe_round import build_policy  # noqa: F401 - 拆分前的顶层名字


if __name__ == "__main__":
    raise SystemExit(main())
