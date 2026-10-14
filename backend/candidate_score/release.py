"""片名解析与硬闸：片名识别表 + 结构合法性过滤。"""

from __future__ import annotations

import math
import re

from .policy import _matches
from .rules import (
    CHS_RULES,
    CODEC_RULES,
    DUAL_AUDIO_RULES,
    DUB_AUDIO_RULES,
    EN_SUB_RULES,
    HARDSUB_RULES,
    RELEASE_GROUPS,
    RESOLUTION_HEIGHTS,
    RESOLUTION_RULES,
    SOURCE_RULES,
    SPAM_RULES,
    SUBTITLE_SCORES,
)


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
