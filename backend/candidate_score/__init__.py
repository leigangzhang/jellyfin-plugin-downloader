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
    speed       下载速度  seeds/peers (and an external index's seeder snapshot)
    experience  观看体验  中文字幕、软字幕、容器、压制组、文件结构
    fit         适配成本  体积区间、合集覆盖、多版本惩罚

Only structural validity and disk safety stay hard filters; those live in
:func:`structural_reject` so the caller can report why a candidate was
dropped.
"""

from __future__ import annotations

from .cli import build_cli_parser, main, parse_args
from .policy import (
    DEFAULT_MIN_SCORE,
    DEFAULT_POLICY,
    DEFAULT_WEIGHTS,
    EXPERIENCE_INNER_WEIGHTS,
    QUALITY_INNER_WEIGHTS,
    _clamp,
    _log_score,
    _matches,
    normalize_policy,
)
from .release import (
    analyze_release,
    bitrate_curve,
    resolution_target_mbps,
    structural_reject,
)
from .rules import (
    CHS_RULES,
    CODEC_RULES,
    CODEC_SCORES,
    CONTAINER_SCORES,
    DUAL_AUDIO_RULES,
    DUB_AUDIO_RULES,
    EN_SUB_RULES,
    HARDSUB_RULES,
    KIND_PENALTIES,
    PAN_EXPERIENCE_INNER_WEIGHTS,
    PAN_RESOLUTION_SCORES,
    RELEASE_GROUPS,
    RESOLUTION_HEIGHTS,
    RESOLUTION_RULES,
    RESOLUTION_SCORES,
    SOURCE_PENALTIES,
    SOURCE_RULES,
    SOURCE_SCORES,
    SPAM_RULES,
    SUBTITLE_SCORES,
)
from .score import (
    _experience_score,
    _fit_score,
    _quality_score,
    apply_score,
    format_candidate_line,
    rank_candidates,
    score_band,
    score_candidate,
)
from .speed import (
    DDHDTV_SOURCE_BONUS,
    DDHDTV_SOURCE_TOKENS,
    EXTERNAL_SEEDERS_DECAY_PER_DAY,
    EXTERNAL_SEEDERS_FRESH_DAYS,
    EXTERNAL_SEEDERS_MIN_DISCOUNT,
    SPEED_DEAD_SWARM,
    SPEED_UNMEASURED_NEUTRAL,
    _external_seeders_signal,
    _seed_signal,
    _speed_score,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "QUALITY_INNER_WEIGHTS",
    "EXPERIENCE_INNER_WEIGHTS",
    "DEFAULT_POLICY",
    "DEFAULT_MIN_SCORE",
    "RESOLUTION_SCORES",
    "PAN_RESOLUTION_SCORES",
    "PAN_EXPERIENCE_INNER_WEIGHTS",
    "RESOLUTION_HEIGHTS",
    "RESOLUTION_RULES",
    "SOURCE_SCORES",
    "SOURCE_RULES",
    "CODEC_SCORES",
    "CODEC_RULES",
    "CONTAINER_SCORES",
    "SUBTITLE_SCORES",
    "CHS_RULES",
    "HARDSUB_RULES",
    "EN_SUB_RULES",
    "DUAL_AUDIO_RULES",
    "DUB_AUDIO_RULES",
    "RELEASE_GROUPS",
    "SOURCE_PENALTIES",
    "KIND_PENALTIES",
    "SPAM_RULES",
    "normalize_policy",
    "_clamp",
    "_log_score",
    "_matches",
    "resolution_target_mbps",
    "bitrate_curve",
    "analyze_release",
    "structural_reject",
    "_quality_score",
    "DDHDTV_SOURCE_TOKENS",
    "DDHDTV_SOURCE_BONUS",
    "SPEED_UNMEASURED_NEUTRAL",
    "SPEED_DEAD_SWARM",
    "EXTERNAL_SEEDERS_FRESH_DAYS",
    "EXTERNAL_SEEDERS_DECAY_PER_DAY",
    "EXTERNAL_SEEDERS_MIN_DISCOUNT",
    "_external_seeders_signal",
    "_seed_signal",
    "_speed_score",
    "_experience_score",
    "_fit_score",
    "score_candidate",
    "apply_score",
    "rank_candidates",
    "score_band",
    "format_candidate_line",
    "build_cli_parser",
    "parse_args",
    "main",
]
