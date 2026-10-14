"""判据表：分辨率 / 片源 / 编码 / 容器 / 字幕 / 音轨 / 压制组与惩罚项的字面量。"""

from __future__ import annotations

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
