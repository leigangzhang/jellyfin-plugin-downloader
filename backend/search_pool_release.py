"""发行名与季号识别：本模块是这些判据的唯一事实源。

给候选池/打分链看的几个纯函数：发行名归一化（`normalise_release_name`）、
去重维度（`release_resolution` / `release_key` / `collapse_release_duplicates`）、
单源判据（`publisher_filter_tokens` / `is_publisher_candidate`）、
集数标记与季号（`candidate_markers` / `cn_number` / `candidate_seasons` /
`strict_season_filter`）。

`build_console.py` 的季号识别（`search_pool.candidate_seasons`）与
tests/test_backend_sources.py 的 SeasonRecognitionHasOneHome 守卫要求季号正则全仓只有
一份实现——拆模块后这一份仍然只在这里，`search_pool` 侧只是重绑定。
"""

import os
import re


# 被偏好的发布者在种子名里的署名（与 candidate_score 的偏好判据同一组）。
# 这是**按名字收窄候选**用的判据；搜索档位的后缀由 source_config 配置。
PUBLISHER_NAME_TOKENS = ("ddhdtv", "高清剧集网")


RESOLUTION_PATTERNS = (
    ("2160p", re.compile(r"2160p|\b4k\b|uhd|3840x2160", re.IGNORECASE)),
    ("1080p", re.compile(r"1080p|1920x1080|\bfhd\b", re.IGNORECASE)),
    ("720p", re.compile(r"720p|1280x720", re.IGNORECASE)),
    ("sd", re.compile(r"480p|576p|dvdrip|hdrip|hdtv|webrip|web-dl|bluray|bdrip", re.IGNORECASE)),
)
MARKETING_NOISE_RE = re.compile(
    r"www\.[a-z0-9.\-]+|[a-z0-9.\-]+\.(?:com|net|org|me|im|tv|cc|xyz)", re.IGNORECASE
)
RELEASE_NOISE_RE = re.compile(r"[\[\]【】()（）{}<>《》|/\\+_,:;!?*·—\-\s.]+")

# Chinese packs are named "15集全" far more often than "S01".
COLLECTION_HINTS = ["全集", "Complete", "全", "S01"]
PACK_PATTERN = re.compile(r"集全|全集|全\d+集|全季|合集|Complete|Season\s*\d+", re.IGNORECASE)
MARKER_PATTERN = re.compile(r"S(\d{1,2})E(\d{1,3})", re.IGNORECASE)
EPISODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:EP|E|第)\s?0*(\d{1,3})(?![\d集全])", re.IGNORECASE)
# 纯编码关键词：S01 / S01E01 / E01。这类词不带语言属性，可以配任何片名
# （中文片名配 S01E01 是常见写法），所以严格匹配时不受「中英不混搭」限制。
CODE_HINT_RE = re.compile(r"(?:[Ss]\d{1,2}(?:[Ee]\d{1,2})?|[Ee]\d{1,3})")


def slugify(value, fallback="pool"):
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value or "").strip("-")
    return (slug or fallback)[:60]


def publisher_filter_tokens(keywords=()):
    """`--publisher-only` 按名字收窄候选面时用的判据（小写、保序去重）。

    取「配置的每条关键字组合」+「组合里按空白切开的每个词」+ 静态署名。两套都要：
    查询后缀是用户写的（可能像 `高清剧集发布网` 这样在索引里查不到），而种子名里的
    署名是 `高清剧集网`——只按其中一套匹配就会把真正的目标漏掉。实测（2026-10-13）
    `漫长的季节 高清剧集发布网` 命中 0 条，而它的名字里确实有 `高清剧集网`。
    """
    tokens = []
    for keyword in keywords or ():
        parts = [str(keyword), *str(keyword).split()]
        for token in parts:
            token = token.strip().lower()
            if token and token not in tokens:
                tokens.append(token)
    for token in PUBLISHER_NAME_TOKENS:
        if token not in tokens:
            tokens.append(token)
    return tuple(tokens)


def is_publisher_candidate(name, tokens=None):
    """候选名是否来自被偏好的发布者（`高清剧集网` / `DDHDTV`）。

    与 `candidate_score.DDHDTV_SOURCE_TOKENS` 是同一份判据的两个用途：那边给总分加分
    （优先级），这边用于 `--publisher-only` 收窄候选面（单一来源）。两边都只看名字，
    因为索引只返回名字，不返回发布者字段。`tokens` 省略时用静态署名；取源链传
    `publisher_filter_tokens(配置的关键字)`，让「配了哪家」决定「只留哪家」。
    """
    text = (name or "").lower()
    return any(token in text for token in (tokens if tokens is not None else PUBLISHER_NAME_TOKENS))


def release_resolution(name):
    """Coarse resolution tag used as one dimension of the de-dupe key."""
    text = name or ""
    for tag, pattern in RESOLUTION_PATTERNS:
        if pattern.search(text):
            return tag
    return "unknown"


def normalise_release_name(name):
    """Strip marketing domains/brackets so two uploads of one release compare equal.

    Deliberately conservative: release-group tags survive (they are plain
    tokens). Dropping them would merge genuinely different encodes into a single
    "duplicate", which is the opposite of what this key is for.
    """
    text = MARKETING_NOISE_RE.sub(" ", name or "")
    text = os.path.splitext(text)[0]
    text = RELEASE_NOISE_RE.sub(" ", text)
    return " ".join(text.lower().split())


def release_key(candidate):
    """De-dupe dimension added on top of the info-hash key.

    The pool is keyed by info hash, and one release routinely arrives as 5+
    different hashes (the audit saw `[第31-36集]`, `全39集` and `Complete`
    variants of one season). Recording `normalised name + resolution + source`
    makes those siblings visible instead of silently competing as unrelated
    entries.
    """
    return "|".join(
        (
            normalise_release_name(candidate.get("name") or ""),
            release_resolution(candidate.get("name") or ""),
            str(candidate.get("source") or ""),
        )
    )


def collapse_release_duplicates(candidates):
    """Keep one representative per `release_key`; alternatives are recorded.

    Off by default in `collect`. Measured 2026-10-13: collapsing on the L1
    sample would take a keyword below the 3x candidate target the same change
    is meant to deliver, so the dimension is reported (`release_groups`) and the
    alternates are listed on the representative instead of being dropped.
    """
    kept = {}
    dropped = {}
    for info_hash, candidate in candidates.items():
        key = candidate.get("release_key") or release_key(candidate)
        representative = kept.get(key)
        if representative is None:
            kept[key] = candidate
            continue
        siblings = representative.setdefault("duplicate_hashes", [])
        siblings.append(info_hash)
        representative.setdefault("duplicate_names", []).append(candidate.get("name") or info_hash)
        dropped.setdefault(key, []).append(info_hash)
    return kept, dropped


def candidate_markers(candidate, season=1):
    """Episode markers a candidate covers, e.g. {'S01E15'} or a pack's range.

    TVBOXNOW-style uploads put the episode number in the file name
    (`... EP15.mp4`) while the torrent name stays generic, so file names are
    checked first. Names flagged as a complete pack are not guessed at.
    """
    texts = [candidate.get("name") or ""]
    texts.extend(
        (entry.get("path") or entry.get("name") or "")
        for entry in candidate.get("videos") or []
    )
    blob = " ".join(texts)
    markers = {
        f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"
        for match in MARKER_PATTERN.finditer(blob)
    }
    if markers:
        return markers
    episode_text = " ".join(
        [
            (entry.get("name") or "")
            for entry in candidate.get("videos") or []
        ]
        + [candidate.get("name") or ""]
    )
    if PACK_PATTERN.search(candidate.get("name") or ""):
        return set()
    return {
        f"S{int(season):02d}E{int(number):02d}"
        for number in EPISODE_PATTERN.findall(episode_text)
        if int(number) > 0
    }


# 季号的几种显式写法：`S02`（裸）、`第2季`/`第二季`、`Season 2`。中文资源站两种混用，
# 只认 `Sxx` 会把「第二季」整批漏成「没表态」——所以三种都要认。
SEASON_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])S(\d{1,2})(?![Ee0-9])", re.IGNORECASE)
SEASON_WORD_RE = re.compile(
    r"(?:第\s*([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*季|season\s*([0-9]{1,2})(?![0-9]))",
    re.IGNORECASE,
)
CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def cn_number(text):
    """中文数字 → int（只处理 1..99）；解析不出来返回 None。"""
    text = (text or "").strip()
    if not text or len(text) > 3:
        return None
    if "十" not in text:
        return CN_DIGITS.get(text) if len(text) == 1 else None
    head, _, tail = text.partition("十")
    if head and head not in CN_DIGITS:
        return None
    if tail and tail not in CN_DIGITS:
        return None
    tens = CN_DIGITS[head] if head else 1
    ones = CN_DIGITS[tail] if tail else 0
    return tens * 10 + ones


def candidate_seasons(candidate):
    """这条候选的名字/文件名里**明确**出现的季号集合；空集 = 它没表态。

    只认显式写法（`SxxEyy`、裸 `Sxx`、`第N季`/`第X季`、`Season N`），不做任何假设：
    `全集`、`全季`、`第11集` 这种不带季号的名字返回空集，由调用方决定怎么处理（严格按季
    时留着，不猜）。三种写法是**并集**：名字里既有「第二季」又有 `S01` 时两边都算，
    宁可留着让人看见，也不因为一处冲突就把它丢掉。
    """
    texts = [candidate.get("name") or ""]
    texts.extend(
        (entry.get("path") or entry.get("name") or "")
        for entry in candidate.get("videos") or []
    )
    blob = " ".join(texts)
    seasons = {int(match.group(1)) for match in MARKER_PATTERN.finditer(blob)}
    seasons |= {int(match.group(1)) for match in SEASON_TOKEN_RE.finditer(blob)}
    for match in SEASON_WORD_RE.finditer(blob):
        word, arabic = match.group(1), match.group(2)
        value = int(word) if word and word.isdigit() else (cn_number(word) if word else None)
        if value is None and arabic:
            value = int(arabic)
        if value:
            seasons.add(value)
    return seasons


def strict_season_filter(candidates, season):
    """严格按季：丢掉「明确属于别的季」的候选，返回 (kept, dropped)。

    锚点档（`片名 + 发布者署名`）不带季标记，会把同一部剧的其它季一起带回来——这是它换来
    召回的代价。没有这道收口，「严格按季」这个默认打开的开关就等于失效（复核报告 F4：
    锚点档在 strict 分支之前生成，下游也没有季标记过滤）。判定只认显式季标记：能确定季号
    且不含目标季的丢掉；确定不了季号的（整包、裸片名、第N集）留着，**不猜**。
    """
    if not season:
        return dict(candidates), []
    kept = {}
    dropped = []
    for info_hash, item in candidates.items():
        seasons = candidate_seasons(item)
        if seasons and int(season) not in seasons:
            dropped.append(
                {
                    "ih": info_hash,
                    "seasons": sorted(seasons),
                    "name": (item.get("name") or "")[:70],
                }
            )
            continue
        kept[info_hash] = item
    return kept, dropped
