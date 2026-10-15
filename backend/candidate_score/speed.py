"""速度子分：seeders 先验、外部索引快照折扣与 dead-swarm 可达性判决。"""

from __future__ import annotations

from .policy import _log_score


# 一次 10s 短窗口实测测不到，不等于这个种子慢：t3-speed.md 实测同一 info hash
# 相邻两次 10s 探针的吞吐可差 26.3×（87.473 vs 3.327 MB/s），跨 hash 排序
# ρ=-0.214。所以实测只能作为加分项，任何一次「0 字节 / 低吞吐」都不允许把候选
# 压到它按 seeders 先验应得的分以下（t1-audit.md R1）。
# 发布者偏好：`高清剧集网发布 www.DDHDTV.com` 是网盘/迅雷系的转载发布，实测同机同
# 时段内它 9.3–9.7MB/s（watch 记录 + 磁盘增量双向印证），而同期两个非该源的候选在
# 30 秒窗口里逻辑字节增长为 0；使用者的历史记录里该源也多次可用。
#
# 它加在**总分**上，不加在任何一个维度里——理由是一次实测踩到的：DDHDTV 候选的体验
# 维度常年已经接近 1.0，把它加在体验上会被维度上限夹掉（实测只值 1.3 分而非 4.4 分）。
# 加在总分上则是一个固定、可审计、可撤销的倾斜量。
#
# 语义边界：它表达的是「这个源在迅雷侧更可能可用」，不是「画质更好」也不是「种子更多」。
# 所以它不参与 QUALITY/EXPERIENCE 权重，也不改其它四个子分——只是在加权和之后加一个
# 有界的常数，量级保证「同片同档位下偏好源优先」，又不足以压过显著的画质/体积差异。
DDHDTV_SOURCE_TOKENS = ("ddhdtv", "高清剧集网")
DDHDTV_SOURCE_BONUS = 4.0

# 没有任何种子信号时的中性速度分；dead-swarm（可达性判决）在 `_speed_score` 里
# 优先于任何先验返回。
SPEED_UNMEASURED_NEUTRAL = 0.40
SPEED_DEAD_SWARM = 0.05

# `breakdown["speed"]["mode"]` 里的取值中，只有这一个不是「分数估计」而是**可达性
# 判决**：announce/live-check 没看到任何不是本机的外部 peer，metadata 必然取不到。
# 排序（`score.rank_candidates`）与候选对象上那个给 UI 读的判据字段都用它，所以写成
# 常量，别在几处各拼一遍字符串。
DEAD_SWARM_MODE = "dead-swarm"


def is_dead_swarm(candidate):
    """这条候选是不是「判定不可达」：过不了提交前的元数据探测。

    两个来源都要认，因为它们出现在不同时机，而且**显式布尔位优先**：

    * 候选对象上的 `dead_swarm` 布尔位——`probe/probe_round.py:188` 在 announce 报
      `external_peers == 0` 时写下的原始判决；live-check 复核拿到 metadata 后会把它
      翻回 False。这个键在就用它，**不再看 breakdown**：否则一条已被复核救回的候选会
      被上一轮的旧明细继续判死。
    * 只有在**没有这个键**时才回落到 `breakdown["speed"]["mode"] == "dead-swarm"`——
      那是 `_speed_score` 落进明细的同一个判决，后端快照与面板读到的 JSON 里只有它。

    只看 `mode == "unknown"` 不算 dead：那是 tracker 抓不到读数，属于「不知道」，不是
    「不可达」——把它当闸门会把新种、私有 tracker 的候选一起沉底。
    """
    if not isinstance(candidate, dict):
        return False
    if "dead_swarm" in candidate:
        return bool(candidate["dead_swarm"])
    speed = (candidate.get("breakdown") or {}).get("speed") or {}
    return speed.get("mode") == DEAD_SWARM_MODE



# 外部索引快照（第二磁力源 torrents-csv）的种子数折扣（t9 报告 F4 的接口契约）：
#   * 字段语义（与 backend/search_pool.torrents_csv_to_candidate 对齐）：
#       seeds / peers               —— announce 实测，只有 probe 能写；
#       external_seeders            —— 外部索引快照的种子数（int，可能缺失/None）；
#       external_seeders_age_days   —— 该快照比当前时刻旧多少天（float，可能缺失）。
#   * 兜底条件：**announce 完全没有读数**（`seeds<=0 and peers<=0`）时才用外部值。
#     注意这只是「先验」层面的优先级；`dead_swarm`（announce 报 external_peers==0）
#     是更早的可达性判决：`_speed_score` 在算完之后直接返回 0.05，**外部值被忽略**。
#     这是刻意设计，不是漏接线——生产控制台不传 `--no-live-check`，默认流程里这类
#     候选会被 live-check 的 metadata 复核救回（metadata_verified=True → dead_swarm=False）；
#     只有手工加 `--no-live-check` 的路径会看到「外部 5000 也被判 0.05」（t13 §3 实测）。
#   * 快照 ≤7 天不打折；每多 1 天衰减 0.02，最低 0.5。
#   * 折扣后的先验不得低于无信号中性 0.40：一片陈旧快照只说明「信息不可靠」，
#     不能因此把一个候选判成「种子很慢」。
EXTERNAL_SEEDERS_FRESH_DAYS = 7.0
EXTERNAL_SEEDERS_DECAY_PER_DAY = 0.02
EXTERNAL_SEEDERS_MIN_DISCOUNT = 0.5


def _external_seeders_signal(candidate):
    """外部快照的 (种子数, 年龄天数, 折扣)；缺失/None/非正值一律视为没有信号。"""
    raw = candidate.get("external_seeders")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        age_days = float(candidate.get("external_seeders_age_days") or 0.0)
    except (TypeError, ValueError):
        age_days = 0.0
    if age_days < 0:
        # 负年龄是坏数据：按「刚刚快照」处理，而不是按未来时间放大折扣。
        age_days = 0.0
    over = max(age_days - EXTERNAL_SEEDERS_FRESH_DAYS, 0.0)
    discount = max(
        EXTERNAL_SEEDERS_MIN_DISCOUNT,
        1.0 - EXTERNAL_SEEDERS_DECAY_PER_DAY * over,
    )
    return value, age_days, discount


def _seed_signal(candidate, policy):
    """速度先验的 (取值, 来源, 明细)。

    来源优先级：``announce``（seeds/peers 有读数）→ ``external``（外部快照兜底，
    仅当 announce **完全没有读数**）→ ``none``（无任何信号）。外部兜底值已经过
    年龄折扣并抬到中性 0.40 之上。

    这里只决定「先验」；``dead_swarm``（不可达判决）在 `_speed_score` 里晚于先验
    但优先于它返回——见该函数的 dead-swarm 分支说明。
    """
    seeds = float(candidate.get("seeds") or 0)
    peers = float(candidate.get("peers") or 0)
    if seeds > 0 or peers > 0:
        seeds_part = _log_score(seeds, policy["seeds_reference"])
        peers_part = _log_score(peers, policy["peers_reference"])
        return (
            0.8 * seeds_part + 0.2 * peers_part,
            "announce",
            {"seeds": int(seeds), "peers": int(peers)},
        )

    external = _external_seeders_signal(candidate)
    if external is None:
        return 0.0, "none", {}

    value, age_days, discount = external
    curve = _log_score(value, policy["seeds_reference"])
    prior = max(SPEED_UNMEASURED_NEUTRAL, discount * curve)
    return (
        prior,
        "external",
        {
            "external_seeders": int(value),
            "external_seeders_age_days": round(age_days, 2),
            "external_seeders_discount": round(discount, 3),
        },
    )


def _speed_score(candidate, policy):
    """速度子分：只剩「多少人正在做种」这一件事。

    分支（`breakdown["speed"]["mode"]`，`score_source` 说明分数由谁决定）：

      * ``dead-swarm`` —— announce/live-check 判定不可达：0.05，**先验（含外部快照）
        在这里被忽略**——可达性判决优先于「有多少种子」的估计（见文件头部
        EXTERNAL_SEEDERS_* 注释里的 `--no-live-check` 说明）。
      * ``unknown``    —— 既没有 announce 实测也没有外部快照：中性 0.40。
      * ``seeds`` / ``seeds-external`` —— 直接用先验；``seed_source`` 区分这条先验
        来自 announce 实测还是外部索引快照。

    没有「实测吞吐」分支了：aria2c 短窗测速在重复测量间散布 1.16–8.50×，排不动候选，
    却让每条候选固定付 17.6–24.1s（见 `probe_magnets` 模块头）。所以
    `measured_speed_mbps` 不再是打分输入，写入它的那条路径也已删除。
    """
    prior, seed_source, signal = _seed_signal(candidate, policy)
    parts = {"seeds_prior": round(prior, 4), "seed_source": seed_source}
    parts.update(signal)

    if candidate.get("dead_swarm"):
        # A live announce showed no peer that is not this machine, so the
        # torrent cannot even produce metadata, let alone download.
        parts["mode"] = DEAD_SWARM_MODE
        parts["external_peers"] = 0
        parts["score_source"] = DEAD_SWARM_MODE
        return SPEED_DEAD_SWARM, parts

    if seed_source == "none":
        # Tracker scrape failed or the torrent is brand new: unknown, not dead.
        parts["mode"] = "unknown"
        parts["seeds"] = 0
        parts["peers"] = 0
        parts["score_source"] = "neutral"
        return SPEED_UNMEASURED_NEUTRAL, parts

    parts["mode"] = "seeds" if seed_source == "announce" else "seeds-external"
    parts["seeds"] = int(float(candidate.get("seeds") or 0))
    parts["peers"] = int(float(candidate.get("peers") or 0))
    parts["score_source"] = "seeds-prior"
    return prior, parts
