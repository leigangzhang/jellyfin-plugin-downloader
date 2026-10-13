#!/usr/bin/env python3
"""One weighted score for every torrent candidate.

Design notes
------------
The previous flow used the skill's core rules as hard gates: a release was
dropped when it was not 1080p, when its estimated bitrate left the 4-6 Mbps
window, or when its size left the 5-10 GB window. A fast, well-seeded 1080p
release with a 3.4 Mbps encode was therefore discarded, while a slow release
with a textbook bitrate was submitted.

This module turns those rules into weights. Every candidate keeps a single
0-100 score built from four dimensions, and the highest score wins:

    quality     画质      resolution, bitrate vs a resolution-aware target,
                          source tier, codec
    speed       下载速度  measured throughput, otherwise seeds/peers
    experience  观看体验  中文字幕、软字幕、容器、压制组、文件结构
    fit         适配成本  体积区间、合集覆盖、多版本惩罚

Only structural validity and disk safety stay hard filters; those live in
:func:`structural_reject` so the caller can report why a candidate was
dropped.
"""

from __future__ import annotations

import argparse
import math
import re

DEFAULT_WEIGHTS = {
    "quality": 0.34,
    "speed": 0.28,
    "experience": 0.26,
    "fit": 0.12,
}

# Inner weights: how each dimension blends its own signals.
QUALITY_INNER_WEIGHTS = {
    "resolution": 0.45,
    "bitrate": 0.33,
    "source": 0.17,
    "codec": 0.05,
}
EXPERIENCE_INNER_WEIGHTS = {
    "subtitle": 0.42,
    "hygiene": 0.22,
    "container": 0.14,
    "release_group": 0.12,
    "audio": 0.10,
}

DEFAULT_POLICY = {
    "kind": "movie",
    "duration_seconds": 7200.0,
    "target_bitrate_mbps": 5.0,
    "target_size_gb": 7.5,
    "preferred_min_gb": 5.0,
    "preferred_max_gb": 10.0,
    "hard_max_gb": 15.0,
    "speed_reference_mbps": 15.0,
    "seeds_reference": 120.0,
    "peers_reference": 200.0,
    "weights": dict(DEFAULT_WEIGHTS),
    "wanted_markers": [],
}

# Below this score the pool is reported as too weak instead of silently
# submitting the best of a bad set. It is a convenience default; callers can
# lower or raise it with --min-score.
DEFAULT_MIN_SCORE = 45.0

RESOLUTION_SCORES = {
    "2160p": 1.00,
    "1080p": 0.86,
    "720p": 0.45,
    "576p": 0.28,
    "480p": 0.18,
    "unknown": 0.42,
}
# Cloud-drive candidates default to 1080p: a cloud "4K" is often a mislabelled
# or upscaled upload, and 1080p is the resolution the library is built around.
PAN_RESOLUTION_SCORES = dict(RESOLUTION_SCORES, **{"2160p": 0.90, "1080p": 1.00})
PAN_EXPERIENCE_INNER_WEIGHTS = {
    "subtitle": 0.52,
    "hygiene": 0.18,
    "container": 0.12,
    "release_group": 0.10,
    "audio": 0.08,
}
RESOLUTION_HEIGHTS = {
    "2160p": 2160,
    "1080p": 1080,
    "720p": 720,
    "576p": 576,
    "480p": 480,
}
RESOLUTION_RULES = [
    ("2160p", r"2160p|(?<![0-9a-z])4k(?![0-9a-z])|uhd|(?<![0-9])2160(?![0-9])"),
    ("1080p", r"1080[pi]|full[ ._-]?hd|(?<![0-9a-z])fhd(?![0-9a-z])"),
    ("720p", r"720[pi]"),
    ("576p", r"576[pi]"),
    ("480p", r"480[pi]|(?<![0-9a-z])sd(?![0-9a-z])"),
]

SOURCE_SCORES = {
    "remux": 1.00,
    "bluray": 0.95,
    "webdl": 0.90,
    "webrip": 0.70,
    "hdtv": 0.50,
    "dvd": 0.45,
    "cam": 0.05,
    "unknown": 0.60,
}
SOURCE_RULES = [
    ("remux", r"remux|blu-?ray[ ._-]?remux|bdremux|原盘|uhd[ ._-]?remux"),
    ("bluray", r"blu-?ray|bdrip|brrip|蓝光"),
    ("webdl", r"web-?dl|webdl|dsnp|disney\+|(?<![a-z])nf(?![a-z])|netflix|amzn|atvp|hmax|itunes|viu|friDay|hbomax|pcok"),
    ("webrip", r"web-?rip|webrip|hdrip|hdtc"),
    ("hdtv", r"hdtv|hdtvrip"),
    ("dvd", r"dvdrip|dvdscr|(?<![a-z0-9])dvd(?![a-z0-9])"),
    ("cam", r"(?<![a-z0-9])cam(?![a-z0-9])|telesync|(?<![a-z0-9])hdts(?![a-z0-9])|(?<![a-z0-9])ts(?![a-z0-9])|枪版|抢先版|盗摄"),
]

CODEC_SCORES = {
    "hevc": 1.00,
    "h264": 0.97,
    "av1": 0.85,
    "mpeg2": 0.45,
    "unknown": 0.90,
}
CODEC_RULES = [
    ("hevc", r"x265|h\.?265|hevc"),
    ("h264", r"x264|h\.?264|(?<![a-z0-9])avc(?![a-z0-9])"),
    ("av1", r"(?<![a-z0-9])av1(?![a-z0-9])"),
    ("mpeg2", r"mpeg-?2|xvid|divx"),
]

CONTAINER_SCORES = {
    ".mkv": 1.00,
    ".mp4": 0.95,
    ".m4v": 0.90,
    ".ts": 0.75,
    ".m2ts": 0.75,
    ".avi": 0.60,
    ".mov": 0.85,
    ".wmv": 0.55,
}

# Chinese subtitles are the user's default viewing mode, so they are a large
# experience component. Burned-in subtitles cost points because they cannot be
# switched off and double up with Jellyfin's own subtitle rendering.
SUBTITLE_SCORES = {
    "chinese-soft": 1.00,
    "chinese-hard": 0.45,
    "english": 0.60,
    "unknown": 0.55,
}
CHS_RULES = [
    r"(?<![a-z0-9])chs(?![a-z0-9])",
    r"(?<![a-z0-9])cht(?![a-z0-9])",
    r"简繁|简体|繁体|中字|中英|国英|双语|官译|中文字幕|中文",
    # 内嵌 (muxed) subtitles are the preferred shape: switchable in the
    # player, no burned-in text.
    r"内嵌中字|内嵌字幕|内嵌简|内嵌繁|内封字幕",
]
HARDSUB_RULES = [
    r"硬字幕|硬sub|硬压|hardsub|烧制|内嵌硬字幕",
]
EN_SUB_RULES = [
    r"(?<![a-z0-9])eng(?![a-z0-9])",
    r"english|英字|英文|(?<![a-z0-9])subs(?![a-z0-9])|多语字幕",
]
DUAL_AUDIO_RULES = [
    r"国英|英国|双语|dual[ ._-]?audio|原声|(?<![a-z0-9])eng(?![a-z0-9])",
]
DUB_AUDIO_RULES = [
    r"国语|国配|粤语|国粤|台配|mandarin|cantonese",
]

RELEASE_GROUPS = [
    "cmct",
    "chd",
    "wiki",
    "frds",
    "galaxy",
    "qhstudio",
    "ourtv",
    "ade",
    "hdchina",
    "hdh",
    "beast",
    "ntb",
    "flux",
    "playweb",
    "tigole",
    "qxr",
    "hhclub",
    "mteam",
    "pter",
    "tjupt",
    "subsplease",
    "nogrp",
    "drones",
    "psa",
]


SOURCE_PENALTIES = {
    # A camcorder/telecine rip is never the version the user wants to keep in
    # the library; it stays selectable but has to lose against anything real.
    "cam": 0.55,
}

# A cloud-drive resource needs a transfer into the account and a retrieve
# download before it is local, and free accounts rate-limit both. It stays
# fully scored, but an equally good magnet wins.
KIND_PENALTIES = {
    "pan": 0.95,
}

# Public search APIs mix adult and netdisk-funnel uploads into film results.
# Those are not "low quality", they are out of scope for a film library, so
# they are the one non-structural reason a candidate is hard-rejected.
SPAM_RULES = [
    r"(?<![a-z0-9])xxx(?![a-z0-9])",
    r"porn",
    r"onlyfans",
    r"sxyprn",
    r"atkgalleria",
    r"hegre|metart|mompov|familystrokes|blacked|brazzers|realitykings",
    r"操逼|群交|内射|无套|骚穴|母狗|大鸡吧|吃鸡啪啪|淫乱|色情|成人视频",
]


def normalize_policy(policy=None):
    """Fill defaults and normalize the weight vector to sum to 1."""
    merged = dict(DEFAULT_POLICY)
    merged["weights"] = dict(DEFAULT_WEIGHTS)
    for key, value in (policy or {}).items():
        if value is None:
            continue
        if key == "weights" and isinstance(value, dict):
            merged["weights"].update(
                {name: float(item) for name, item in value.items()}
            )
        else:
            merged[key] = value
    total = sum(
        max(float(merged["weights"].get(name, 0.0)), 0.0) for name in DEFAULT_WEIGHTS
    )
    if total <= 0:
        merged["weights"] = dict(DEFAULT_WEIGHTS)
    else:
        merged["weights"] = {
            name: max(float(merged["weights"].get(name, 0.0)), 0.0) / total
            for name in DEFAULT_WEIGHTS
        }
    return merged


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _log_score(value, reference):
    """Saturating 0-1 curve for counts and speeds."""
    if value is None or value <= 0:
        return 0.0
    if reference <= 0:
        return 0.0
    return _clamp(math.log1p(value) / math.log1p(reference))


def _matches(patterns, text):
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def resolution_target_mbps(policy):
    """Scale the preferred bitrate with the candidate's pixel count."""
    base = float(policy.get("target_bitrate_mbps") or 5.0)
    height = RESOLUTION_HEIGHTS.get(
        policy.get("target_resolution") or "1080p", 1080
    )
    return base * (height / 1080.0) ** 2


def bitrate_curve(bitrate_mbps, target_mbps):
    """Reward bitrate above/at target, punish heavy compression strongly.

    The low side decays in log space: an encode at 40% of target is not just a
    bit worse, it shows blocking and banding in dark scenes and fast motion.
    The high side saturates quickly; quality is not the place to charge for
    disk space, the `fit` dimension does that.
    """
    if bitrate_mbps is None or bitrate_mbps <= 0:
        return 0.45
    if target_mbps <= 0:
        return 0.60
    ratio = bitrate_mbps / target_mbps
    if ratio < 1.0:
        return math.exp(-(math.log(ratio) ** 2) / (2 * 0.45 ** 2))
    return 1.0 - 0.10 * math.exp(-(ratio - 1.0) / 0.40)


def analyze_release(name):
    """Detect resolution, source, codec, subtitles, audio and release group."""
    text = name or ""
    resolution = "unknown"
    for label, pattern in RESOLUTION_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            resolution = label
            break

    source = "unknown"
    for label, pattern in SOURCE_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            source = label
            break

    codec = "unknown"
    for label, pattern in CODEC_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            codec = label
            break

    hard_sub = _matches(HARDSUB_RULES, text)
    has_chs = _matches(CHS_RULES, text) or hard_sub
    if hard_sub:
        subtitle_kind = "chinese-hard"
    elif has_chs:
        subtitle_kind = "chinese-soft"
    elif _matches(EN_SUB_RULES, text):
        subtitle_kind = "english"
    else:
        subtitle_kind = "unknown"

    if _matches(DUAL_AUDIO_RULES, text):
        audio_kind = "dual"
    elif _matches(DUB_AUDIO_RULES, text):
        audio_kind = "dub"
    else:
        audio_kind = "unknown"

    lowered = text.lower()
    release_group = next(
        (group for group in RELEASE_GROUPS if group in lowered),
        "",
    )

    return {
        "resolution": resolution,
        "source": source,
        "codec": codec,
        "subtitle_kind": subtitle_kind,
        "subtitle_score": SUBTITLE_SCORES[subtitle_kind],
        "has_chs": has_chs,
        "hard_sub": hard_sub,
        "audio_kind": audio_kind,
        "release_group": release_group,
    }


def structural_reject(candidate, policy):
    """Hard filters: only validity and outright disk danger.

    Everything that used to be a taste rule (resolution, bitrate band, size
    band, subtitles) is scored instead. What remains here would break the
    pipeline or fill the volume: unreadable metadata, a torrent with no usable
    video, or a single video beyond the disk-safety ceiling.

    本模块对「文件清单」的依赖（与 `metadata_at` 的语义边界，见 t9 报告 F6/N7）：
      * `metadata_at` 由 probe/search_pool 写在**池条目**上，含义只有一个——
        **该候选的文件清单已被核实**（probe 从 swarm 真解析出 metadata，或索引
        直接给出 `files`）。它不进候选字典，本模块也不读它。
      * 本模块读的是那份清单的**投影**：`videos` / `main_video_gb` /
        `main_video_ext` / `nfiles` / `junk_files`；下面的硬闸就是「投影里没有
        ≥100MB 的可读视频 → 剔除」。所以「清单被核实」与「清单里有可播内容」
        是两件事：前者由 `metadata_at` 表达，后者由 `videos` 表达。
      * `metadata_missing` 是**另一条路径的**逃逸闸（submit 侧的 `--trust-magnet`：
        提交时用迅雷自己的文件清单），backend 里没有任何生产者；本模块只在两处
        消费它——这里的提前放行（不过硬闸）与总分 ×0.85（:734）。
    """
    if candidate.get("metadata_missing"):
        # --trust-magnet path: Thunder's own file list is used at submit time.
        return ""
    if SPAM_RULES and _matches(SPAM_RULES, candidate.get("name") or ""):
        return "not a film/TV release (adult or spam magnet)"
    if candidate.get("pre_transfer"):
        # A cloud share whose file list is not readable yet: it is scored on
        # its title now and re-scored after the transfer reveals the files.
        return ""
    if not candidate.get("videos"):
        return "no readable video file >=100MB in torrent metadata"
    size = float(candidate.get("main_video_gb") or 0)
    hard_max = float(policy.get("hard_max_gb") or 0)
    if hard_max > 0 and size > hard_max:
        return (
            f"video {size:.1f}GB exceeds the {hard_max:.0f}GB disk-safety ceiling"
        )
    return ""


def _quality_score(candidate, attrs, target_mbps):
    resolution = candidate.get("resolution") or attrs["resolution"]
    source = candidate.get("source") or attrs["source"]
    codec = candidate.get("codec") or attrs["codec"]
    bitrate = candidate.get("estimated_bitrate_mbps")
    resolution_scores = (
        PAN_RESOLUTION_SCORES
        if candidate.get("kind") == "pan"
        else RESOLUTION_SCORES
    )

    parts = {
        "resolution": resolution_scores.get(resolution, resolution_scores["unknown"]),
        "bitrate": bitrate_curve(bitrate, target_mbps),
        "source": SOURCE_SCORES.get(source, SOURCE_SCORES["unknown"]),
        "codec": CODEC_SCORES.get(codec, CODEC_SCORES["unknown"]),
    }
    score = sum(
        parts[name] * weight for name, weight in QUALITY_INNER_WEIGHTS.items()
    )
    return score, parts


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

SPEED_MEASURED_MIN_MBPS = 0.001
SPEED_UNMEASURED_NEUTRAL = 0.40
SPEED_DEAD_SWARM = 0.05



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


def _seeds_prior(candidate, policy):
    """速度先验（0-1），同时是速度子分的下限。

    被抽中测速的候选也要走这条曲线做 `max()`，因此「被抽中」不会比同 swarm 的
    「没被抽中」更差。announce 无实测时由外部快照兜底（见 `_seed_signal`）。
    """
    return _seed_signal(candidate, policy)[0]


def _usable_measurement(measured):
    """可用的实测吞吐（MB/s）；0/None/≤0.001 一律视为「未测到」。"""
    if measured is None:
        return None
    value = float(measured)
    if value <= SPEED_MEASURED_MIN_MBPS:
        return None
    return value


def _speed_floor(candidate, prior, has_signal):
    """不测量时这个候选应得的速度分——实测永远不得低于它。

    有任何种子信号（announce 实测或外部快照）时就是先验；毫无信号时是中性 0.40
    （``unknown`` 分支的取值），否则一条「0.5MB/s」的实测会反过来把无信号候选
    从 0.40 压到 0.15。
    """
    return prior if has_signal else SPEED_UNMEASURED_NEUTRAL


def _speed_score(candidate, policy):
    """速度子分：seeders 先验是下限，短窗口实测只能加分。

    分支（`breakdown["speed"]["mode"]`，另给 `score_source` / `seed_source` 说明
    分数由谁决定）：
      * ``measured``        —— 有 >0.001MB/s 的实测：取 ``max(不测量时应得的分, 实测曲线)``，
                               ``score_source`` = ``measured`` / ``seeds-prior`` / ``neutral``。
      * ``unmeasured``      —— 元数据可解析但窗口内没测到吞吐（含 0/≤0.001 的实测）：
                               ``max(0.40 中性, 先验)``。
      * ``dead-swarm``      —— announce/live-check 判定不可达：仍是 0.05，**先验
                               （含外部快照）在这里被忽略**——可达性判决优先于
                               「有多少种子」的估计，且这是刻意设计（见文件头部
                               EXTERNAL_SEEDERS_* 注释里的 `--no-live-check` 说明）。
      * ``unknown``         —— 既没有 announce 实测也没有外部快照：中性 0.40。
      * ``seeds`` / ``seeds-external`` —— 没被抽中测速，直接用先验；``seed_source``
                               区分这条先验来自 announce 实测还是外部索引快照。
    """
    measured = _usable_measurement(candidate.get("measured_speed_mbps"))
    seeds = float(candidate.get("seeds") or 0)
    peers = float(candidate.get("peers") or 0)
    prior, seed_source, signal = _seed_signal(candidate, policy)
    has_signal = seed_source != "none"
    floor = _speed_floor(candidate, prior, has_signal)
    parts = {
        "seeds_prior": round(prior, 4),
        "speed_floor": round(floor, 4),
        "seed_source": seed_source,
    }
    parts.update(signal)
    if measured is None and candidate.get("measured_speed_mbps") is not None:
        # 探针写了 0/≤0.001：诊断要留下「测过但不算证据」，分数不许被它影响。
        parts["measured_discarded"] = round(
            float(candidate.get("measured_speed_mbps") or 0.0), 3
        )

    if measured is not None:
        curve = _log_score(measured, policy["speed_reference_mbps"])
        parts["mode"] = "measured"
        parts["measured_mbps"] = round(measured, 3)
        if curve >= floor:
            parts["score_source"] = "measured"
            return curve, parts
        # 实测低于「不测量时应得的分」：只说明「这个窗口不快」，不足以把候选打低。
        parts["score_source"] = "seeds-prior" if has_signal else "neutral"
        return floor, parts

    if candidate.get("speed_unmeasured"):
        # 元数据可解析（种子可达），但短窗口内没测到吞吐。既不判死种，也不能低于
        # 同 swarm 的未抽中候选——后者正是改进前的逆激励。
        parts["mode"] = "unmeasured"
        parts["reason"] = "metadata ok, no throughput in window"
        score = max(SPEED_UNMEASURED_NEUTRAL, prior)
        parts["score_source"] = (
            "seeds-prior" if prior > SPEED_UNMEASURED_NEUTRAL else "neutral"
        )
        return score, parts

    if candidate.get("dead_swarm"):
        # A live announce showed no peer that is not this machine, so the
        # torrent cannot even produce metadata, let alone download.
        parts["mode"] = "dead-swarm"
        parts["external_peers"] = 0
        parts["score_source"] = "dead-swarm"
        return SPEED_DEAD_SWARM, parts

    if not has_signal:
        # Tracker scrape failed or the torrent is brand new: unknown, not dead.
        parts["mode"] = "unknown"
        parts["seeds"] = 0
        parts["peers"] = 0
        parts["score_source"] = "neutral"
        return SPEED_UNMEASURED_NEUTRAL, parts

    parts["mode"] = "seeds" if seed_source == "announce" else "seeds-external"
    parts["seeds"] = int(seeds)
    parts["peers"] = int(peers)
    parts["score_source"] = "seeds-prior"
    return prior, parts


def _experience_score(candidate, attrs, policy):
    videos = candidate.get("videos") or []
    main_gb = float(candidate.get("main_video_gb") or 0)
    nfiles = int(candidate.get("nfiles") or len(videos) or 1)
    junk = int(candidate.get("junk_files") or 0)
    junk_bytes = float(candidate.get("junk_bytes") or 0)
    total_bytes = float(candidate.get("size_gb") or 0) * 1024 ** 3

    big_videos = [
        item
        for item in videos
        if main_gb > 0
        and float(item.get("size") or 0) / (1024 ** 3) >= 0.4 * main_gb
    ]
    if policy["kind"] == "movie":
        if len(big_videos) <= 1:
            hygiene = 1.00
        elif len(big_videos) == 2:
            hygiene = 0.85
        else:
            hygiene = 0.60
    elif len(videos) <= 1:
        hygiene = 1.00
    elif len(videos) <= 30:
        hygiene = 0.95
    else:
        hygiene = 0.80
    # Ad/junk weight by bytes first (a pack that is 40% cover images really is
    # bad) and by count second (a video plus twenty funnel files is a warning
    # sign even when the bytes are tiny).
    byte_ratio = junk_bytes / total_bytes if total_bytes > 0 else 0.0
    count_ratio = junk / max(nfiles, 1)
    junk_penalty = min(
        1.0,
        0.6 * min(1.0, byte_ratio * 3.0) + 0.3 * min(1.0, count_ratio),
    )
    hygiene *= 1.0 - junk_penalty

    extension = str(candidate.get("main_video_ext") or "").lower()
    container = CONTAINER_SCORES.get(extension, 0.80)

    audio = {"dual": 1.00, "dub": 0.80, "unknown": 0.85}[attrs["audio_kind"]]
    group = 1.00 if attrs["release_group"] else 0.70

    parts = {
        "subtitle": attrs["subtitle_score"],
        "hygiene": hygiene,
        "container": container,
        "release_group": group,
        "audio": audio,
    }
    inner_weights = (
        PAN_EXPERIENCE_INNER_WEIGHTS
        if candidate.get("kind") == "pan"
        else EXPERIENCE_INNER_WEIGHTS
    )
    score = sum(parts[name] * weight for name, weight in inner_weights.items())
    return score, parts


def _fit_score(candidate, policy):
    size = float(candidate.get("main_video_gb") or 0)
    target = float(policy.get("target_size_gb") or 0) or max(
        float(policy.get("preferred_min_gb") or 0), 1.0
    )
    low = float(policy.get("preferred_min_gb") or 0)
    high = float(policy.get("preferred_max_gb") or target)

    if size <= 0:
        size_score = 0.45
    elif low <= size <= high:
        size_score = _clamp(1.0 - 0.3 * abs(math.log(size / target)), 0.55, 1.0)
    elif size < low:
        size_score = 0.60 * math.exp(
            -((math.log(size / low)) ** 2) / (2 * 0.55 ** 2)
        )
    else:
        size_score = 0.60 * math.exp(
            -((math.log(size / high)) ** 2) / (2 * 0.30 ** 2)
        )

    coverage = 1.0
    wanted = policy.get("wanted_markers") or []
    if policy["kind"] == "episode" and wanted:
        covered = set(candidate.get("covered_markers") or [])
        coverage = _clamp(len(covered & set(wanted)) / len(wanted), 0.35, 1.0)

    version_penalty = 1.0
    if policy["kind"] == "movie" and candidate.get("extra_version"):
        version_penalty = 0.85

    score = (0.85 * size_score + 0.15 * coverage) * version_penalty
    return score, {
        "size": round(size_score, 4),
        "coverage": round(coverage, 4),
        "target_gb": round(target, 3),
    }


def score_candidate(candidate, policy=None):
    """Return the 0-100 score plus a per-dimension breakdown.

    `candidate` is the enriched probe record: name, videos, nfiles,
    main_video_gb, estimated_bitrate_mbps, seeds, peers and optionally
    measured_speed_mbps (MB/s), main_video_ext and covered_markers.
    """
    policy = normalize_policy(policy)
    attrs = analyze_release(candidate.get("name") or "")

    target_mbps = resolution_target_mbps(
        {
            **policy,
            "target_resolution": candidate.get("resolution") or attrs["resolution"],
        }
    )
    quality, quality_parts = _quality_score(candidate, attrs, target_mbps)
    speed, speed_parts = _speed_score(candidate, policy)
    experience, experience_parts = _experience_score(candidate, attrs, policy)
    fit, fit_parts = _fit_score(candidate, policy)

    dimensions = {
        "quality": quality,
        "speed": speed,
        "experience": experience,
        "fit": fit,
    }
    weights = policy["weights"]
    total = 100.0 * sum(dimensions[name] * weights[name] for name in dimensions)
    # 发布者偏好：有界的源可用性倾斜（见文件头部 DDHDTV_SOURCE_* 注释）。
    # 命中时把它记进 breakdown，好让「为什么这条排在前面」在面板与审计里有据可查。
    name_lower = (candidate.get("name") or "").lower()
    source_preference = (
        DDHDTV_SOURCE_BONUS
        if any(token in name_lower for token in DDHDTV_SOURCE_TOKENS)
        else 0.0
    )
    total += source_preference
    if candidate.get("metadata_missing"):
        total *= 0.85
    total *= SOURCE_PENALTIES.get(attrs["source"], 1.0)
    total *= KIND_PENALTIES.get(candidate.get("kind") or "magnet", 1.0)
    if candidate.get("penalty"):
        total *= float(candidate["penalty"])

    return {
        "score": round(total, 2),
        # A pre-transfer cloud share is scored on its name only; the number is
        # provisional until the real file list is read after the transfer.
        "provisional": bool(candidate.get("pre_transfer")),
        "dimensions": {
            name: round(value * 100.0, 2) for name, value in dimensions.items()
        },
        "breakdown": {
            "quality": {
                name: round(value, 4) for name, value in quality_parts.items()
            },
            "speed": speed_parts,
            "experience": {
                name: round(value, 4) for name, value in experience_parts.items()
            },
            "fit": fit_parts,
            "target_bitrate_mbps": round(target_mbps, 2),
            "source_preference": source_preference,
        },
        "attrs": attrs,
    }


def apply_score(candidate, policy=None):
    """Attach score fields to a candidate dict in place and return it."""
    result = score_candidate(candidate, policy)
    candidate["score"] = result["score"]
    candidate["provisional"] = result["provisional"]
    candidate["dimensions"] = result["dimensions"]
    candidate["breakdown"] = result["breakdown"]
    for key, value in result["attrs"].items():
        if key in ("subtitle_kind", "subtitle_score") or candidate.get(key):
            continue
        candidate[key] = value
    return candidate


def rank_candidates(candidates, policy=None):
    """Score every candidate and return them sorted best-first."""
    policy = normalize_policy(policy)
    for candidate in candidates:
        apply_score(candidate, policy)
    candidates.sort(
        key=lambda item: (
            item.get("score", 0.0),
            item.get("seeds", 0),
            -abs(float(item.get("main_video_gb") or 0)),
        ),
        reverse=True,
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate["rank"] = index
    return candidates


def score_band(score):
    if score >= 75:
        return "优秀"
    if score >= 60:
        return "良好"
    if score >= DEFAULT_MIN_SCORE:
        return "可接受"
    return "不推荐"


def format_candidate_line(candidate, index=None):
    """Compact one-line report used by the probe CLI."""
    dimensions = candidate.get("dimensions") or {}
    prefix = f"{index:>2}" if index is not None else "  "
    speed = candidate.get("measured_speed_mbps")
    if candidate.get("kind") == "pan":
        speed_text = "   云盘"
    elif speed is not None:
        speed_text = f"{speed:5.2f}MB/s"
    else:
        speed_text = f"{candidate.get('seeds', 0):5d}seed"
    break_down = "/".join(
        f"{dimensions.get(name, 0):.0f}"
        for name in ("quality", "speed", "experience", "fit")
    )
    return (
        f"{prefix} {candidate.get('score', 0):6.2f} [{break_down}] "
        f"{speed_text} "
        f"{candidate.get('resolution', '?'):>7} "
        f"{float(candidate.get('main_video_gb') or 0):5.2f}GB "
        f"{'CHS' if candidate.get('has_chs') else '---'} "
        f"{str(candidate.get('name') or '')[:58]}"
    )


def build_cli_parser():
    """这个模块的 `--help` 入口（它本身是库，没有独立的打分 CLI）。

    改动前 `python3 backend/candidate_score.py --help` 是空过（stdout 0 字节、
    退出码 0），验收口径拿不到任何说明（t9 报告 F7）。这里把它变成可读描述：
    说明维度、阈值与对外接口，`--show-weights` 顺带打印默认口径供人工核对。
    """
    parser = argparse.ArgumentParser(
        prog="candidate_score.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "候选打分库：把四个维度合成一个 0-100 分。\n"
            f"  画质 quality {DEFAULT_WEIGHTS['quality']:.2f}"
            f"｜速度 speed {DEFAULT_WEIGHTS['speed']:.2f}"
            f"｜观看体验 experience {DEFAULT_WEIGHTS['experience']:.2f}"
            f"｜适配成本 fit {DEFAULT_WEIGHTS['fit']:.2f}\n"
            "本文件是库，由 probe_magnets.py / console_server.py 导入使用，\n"
            "没有独立的打分 CLI；--help 只说明这层契约。"
        ),
        epilog=(
            "对外接口：normalize_policy / score_candidate / apply_score / "
            "rank_candidates / structural_reject / score_band / "
            "format_candidate_line"
        ),
    )
    parser.add_argument(
        "--show-weights",
        action="store_true",
        help="打印默认四维权重与分数阈值后退出（离线，便于核对口径）",
    )
    return parser


def parse_args(argv=None):
    return build_cli_parser().parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.show_weights:
        print(
            "默认权重: "
            + " | ".join(f"{name} {value:.2f}" for name, value in DEFAULT_WEIGHTS.items())
        )
        print(
            f"默认阈值: min_score {DEFAULT_MIN_SCORE:g}；"
            f"band ≥75 优秀 / ≥60 良好 / ≥{DEFAULT_MIN_SCORE:g} 可接受"
        )
        return 0
    build_cli_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
