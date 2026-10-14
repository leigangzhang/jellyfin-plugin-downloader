"""四维打分与排序：quality / speed / experience / fit 合成一个 0-100 分。"""

from __future__ import annotations

import math

from .policy import (
    DEFAULT_MIN_SCORE,
    EXPERIENCE_INNER_WEIGHTS,
    QUALITY_INNER_WEIGHTS,
    _clamp,
    normalize_policy,
)
from .release import analyze_release, bitrate_curve, resolution_target_mbps
from .rules import (
    CODEC_SCORES,
    CONTAINER_SCORES,
    KIND_PENALTIES,
    PAN_EXPERIENCE_INNER_WEIGHTS,
    PAN_RESOLUTION_SCORES,
    RESOLUTION_SCORES,
    SOURCE_PENALTIES,
    SOURCE_SCORES,
)
from .speed import (
    DDHDTV_SOURCE_BONUS,
    DDHDTV_SOURCE_TOKENS,
    _speed_score,
)


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
    external_seeders / external_seeders_age_days, main_video_ext and
    covered_markers.
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
    if candidate.get("kind") == "pan":
        seed_text = "   云盘"
    else:
        seed_text = f"{candidate.get('seeds', 0):5d}seed"
    break_down = "/".join(
        f"{dimensions.get(name, 0):.0f}"
        for name in ("quality", "speed", "experience", "fit")
    )
    return (
        f"{prefix} {candidate.get('score', 0):6.2f} [{break_down}] "
        f"{seed_text} "
        f"{candidate.get('resolution', '?'):>7} "
        f"{float(candidate.get('main_video_gb') or 0):5.2f}GB "
        f"{'CHS' if candidate.get('has_chs') else '---'} "
        f"{str(candidate.get('name') or '')[:58]}"
    )
