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
"""

import argparse
import base64
import concurrent.futures
import copy
import datetime as dt
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from candidate_score import (
    DEFAULT_MIN_SCORE,
    DEFAULT_WEIGHTS,
    format_candidate_line,
    normalize_policy,
    rank_candidates,
    score_band,
    structural_reject,
)
from media_download_lib import GB, JUNK_EXTS, POOL_DIR, VIDEO_EXTS

# Tracker 列表只保留本机实测可达的项（2026-10-13 实测，守卫在 tests/test_backend_probe.py）：
#
#   tracker.opentrackr.org:1337   announce ✓0.56s / scrape ✓0.71s（85B，含真实种子数）
#   bt1.archive.org:6969          announce ✓0.49s / scrape ✗（HTTP 200 但返回空的 files 字典）
#   tracker2.dler.org:80          announce ✓0.68s / scrape ✓0.73s（82B）
#
# 已剔除（留着只会把每条候选的最坏等待从 0.5s 拉到 8s）：
#   explodie.org:6969             8.00s 超时
#   tracker.openbittorrent.com:80 HTTP 403（TCP 通，announce 被拒）
#   tracker.torrent.eu.org:451    连接被拒
HTTP_TRACKERS = [
    "http://tracker.opentrackr.org:1337/scrape",
    "http://tracker2.dler.org:80/scrape",
]
ANNOUNCE_TRACKERS = [
    "http://tracker.opentrackr.org:1337/announce",
    "http://bt1.archive.org:6969/announce",
    "http://tracker2.dler.org:80/announce",
]

# DHT 是 metadata 的实际来源：同一份实测里，只用上面这几个 HTTP tracker 而把
# DHT 关掉时，30s 内连 metadata 都拿不到（announce 有 peer 也没用）。所以
# fetch_metadata 显式带上 --enable-dht=true —— 这条路径上不许关 DHT。
ARIA2_DHT_FLAG = "--enable-dht=true"

ANNOUNCE_TIMEOUT_SECONDS = 8
SCRAPE_TIMEOUT_SECONDS = 5
# 剩余预算低于这个值就不再发起新的网络调用：一个注定超时的请求只会把
# 「软预算」变成「硬超时」，还不如直接把阶段标记成 timed out。
# 各阶段的最小预算按实测单条耗时定（见 t3/t4 记录）：
#   health    announce/scrape 实测 0.5–0.9s
#   metadata  成功 6.19–8.44s（失败 26.6s），低于 5s 基本是白跑
MIN_NETWORK_BUDGET_SECONDS = 0.5
MIN_HEALTH_BUDGET_SECONDS = 1.0
MIN_METADATA_BUDGET_SECONDS = 5.0

METADATA_TTL_SECONDS = 7 * 24 * 3600
HEALTH_TTL_SECONDS = 3600
MIN_VIDEO_BYTES = 100 * 1024 ** 2

# 池子会被 metadata 阶段的 worker 线程写入，同时主线程要逐条落盘；没有锁的话
# json.dump 可能撞上 dict 变更（RuntimeError: dictionary changed size）。
_POOL_LOCK = threading.Lock()


class SoftBudget:
    """整轮 probe 的挂钟软预算。

    `--budget-secs 0`（默认）表示不限。一旦超出预算：
      * 各阶段不再发起**新的**网络调用（已缓存的候选照常走完，因为它们不花钱）；
      * 阶段名记进 `expired_stages`，第一个超时的阶段写进 `timeout_stage`；
      * 调用方把 partial/timeout_stage 落进 probe JSON（池子照常逐条落盘），
        而不是整轮白跑。

    预算只管住「新工作」，不打断已经在跑的 fetch：所以取超时值时要把它
    clamp 到剩余预算（见 fetch_metadata 的 deadline 参数），否则「软」预算
    会退化成「每条最多 timeout+10s」的硬超时。
    """

    def __init__(self, total_seconds, clock=time.monotonic):
        self._clock = clock
        self.total_seconds = float(total_seconds or 0)
        self.started_at = clock()
        self.expired_stages = []
        self.timeout_stage = ""

    @property
    def enabled(self):
        return self.total_seconds > 0

    @property
    def deadline(self):
        return self.started_at + self.total_seconds

    def remaining(self):
        if not self.enabled:
            return float("inf")
        return max(self.deadline - self._clock(), 0.0)

    def out_of_time(self, stage, need=MIN_NETWORK_BUDGET_SECONDS):
        """True when fewer than `need` seconds remain for starting `stage` work.

        记下第一个「不够跑」的阶段，这样即使预算没归零也能报告 timeout_stage：
        剩 3s 还去开一个 20s 的速度探针，本身就是注定超时。
        """
        if not self.enabled:
            return False
        if self.remaining() >= need:
            return False
        if not self.timeout_stage:
            self.timeout_stage = stage
        if stage not in self.expired_stages:
            self.expired_stages.append(stage)
        return True

    def clamp_timeout(self, wanted):
        """Per-call timeout never longer than the remaining budget."""
        if not self.enabled:
            return wanted
        return max(min(float(wanted), self.remaining()), 0.0)


class StageTimers:
    """Per-stage wall clock, so `partial` claims are checkable instead of asserted."""

    def __init__(self):
        self.seconds = {}

    def start(self):
        return time.monotonic()

    def done(self, name, started):
        self.seconds[name] = round(time.monotonic() - started, 3)
        return self.seconds[name]


def bdecode(data, idx=0):
    """Minimal bencode decoder for tracker scrape responses."""
    char = data[idx : idx + 1]
    if char == b"i":
        end = data.index(b"e", idx)
        return int(data[idx + 1 : end]), end + 1
    if char == b"l":
        idx += 1
        items = []
        while data[idx : idx + 1] != b"e":
            value, idx = bdecode(data, idx)
            items.append(value)
        return items, idx + 1
    if char == b"d":
        idx += 1
        result = {}
        while data[idx : idx + 1] != b"e":
            key, idx = bdecode(data, idx)
            value, idx = bdecode(data, idx)
            result[key] = value
        return result, idx + 1
    colon = data.index(b":", idx)
    length = int(data[idx:colon])
    start = colon + 1
    return data[start : start + length], start + length


def parse_magnet(uri):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
    for value in query.get("xt", []):
        if value.lower().startswith("urn:btih:"):
            return value.split(":")[-1]
    return None


def decode_thunder_link(value):
    raw = value.split("://", 1)[1]
    raw += "=" * (-len(raw) % 4)
    decoded = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    if decoded.startswith("AA") and decoded.endswith("ZZ"):
        decoded = decoded[2:-2]
    return decoded


def parse_candidate(value):
    value = value.strip()
    if not value or value.startswith("#"):
        return None
    if value.startswith("magnet:"):
        return parse_magnet(value)
    if value.lower().startswith("thunder://"):
        try:
            decoded = decode_thunder_link(value)
        except (ValueError, UnicodeError):
            return None
        if decoded.startswith("magnet:"):
            return parse_magnet(decoded)
        if re.fullmatch(r"[A-Fa-f0-9]{40}", decoded):
            return decoded
        return None
    if re.fullmatch(r"[A-Fa-f0-9]{40}", value):
        return value
    return None


def scrape_tracker(tracker_url, info_hash_bytes, timeout=SCRAPE_TIMEOUT_SECONDS):
    separator = "&" if "?" in tracker_url else "?"
    url = (
        f"{tracker_url}{separator}info_hash="
        f"{urllib.parse.quote_from_bytes(info_hash_bytes)}"
    )
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "qBittorrent/4.6"},
        )
        raw = urllib.request.urlopen(request, timeout=timeout).read()
        decoded, _ = bdecode(raw)
        for info in decoded.get(b"files", {}).values():
            return (
                int(info.get(b"complete", 0)),
                int(info.get(b"incomplete", 0)),
            )
    except (OSError, ValueError, KeyError):
        return None
    return None


def tracker_health(info_hash, timeout=SCRAPE_TIMEOUT_SECONDS):
    """Return (seeds, peers) from the first tracker that answers.

    `timeout` is per tracker, not for the whole loop: the caller clamps it to the
    remaining soft budget so a dead tracker cannot eat the round.
    """
    try:
        info_hash_bytes = bytes.fromhex(info_hash)
    except ValueError:
        return 0, 0
    for tracker in HTTP_TRACKERS:
        result = scrape_tracker(tracker, info_hash_bytes, timeout)
        if result:
            return result
    return 0, 0


def public_ip(timeout=5):
    """Own public IPv4, used to ignore the machine's own announces."""
    cache = "/tmp/jellyfin_own_ip.json"
    try:
        with open(cache, "r", encoding="utf-8") as handle:
            cached = json.load(handle)
        if time.time() - float(cached.get("at") or 0) < 1800:
            return cached.get("ip") or ""
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    ip = ""
    try:
        request = urllib.request.Request(
            "https://ifconfig.me", headers={"User-Agent": "curl/8.0"}
        )
        ip = urllib.request.urlopen(request, timeout=timeout).read().decode().strip()
    except (OSError, ValueError):
        ip = ""
    try:
        with open(cache, "w", encoding="utf-8") as handle:
            json.dump({"at": time.time(), "ip": ip}, handle)
    except OSError:
        pass
    return ip


def announce_health(info_hash, own_ips=(), timeout=ANNOUNCE_TIMEOUT_SECONDS):
    """Real swarm health from an HTTP announce, excluding our own announces.

    A scrape only reports a cached number, and once this machine announces the
    tracker also counts *our* client (it shows up under the local public IP,
    and under the VPN exit IP when the tunnel is up). Announce instead, look at
    the actual peer list, and drop any peer that is us.

    `event=stopped` is deliberately not sent: leaving a peer entry behind is
    harmless here (nothing listens on 6881), while a second announce per
    candidate would cost another 0.5-0.9s on the hot path this round is trying
    to shorten.
    """
    try:
        info_hash_bytes = bytes.fromhex(info_hash)
    except ValueError:
        return None
    own = {ip for ip in own_ips if ip}
    peer_id = "-JF2940-" + hashlib.md5(info_hash.encode()).hexdigest()[:12]
    for tracker in ANNOUNCE_TRACKERS:
        query = [
            "info_hash=" + urllib.parse.quote_from_bytes(info_hash_bytes),
            "peer_id=" + urllib.parse.quote(peer_id, safe=""),
            "port=6881",
            "uploaded=0",
            "downloaded=0",
            "left=1048576",
            "compact=1",
            "event=started",
        ]
        url = tracker + "?" + "&".join(query)
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Transmission/2.94"}
            )
            payload, _ = bdecode(
                urllib.request.urlopen(request, timeout=timeout).read()
            )
        except (OSError, ValueError, KeyError):
            continue
        raw_peers = payload.get(b"peers")
        peers = []
        if isinstance(raw_peers, bytes):
            for index in range(0, len(raw_peers) // 6 * 6, 6):
                peers.append(
                    (
                        socket.inet_ntoa(raw_peers[index : index + 4]),
                        struct.unpack(">H", raw_peers[index + 4 : index + 6])[0],
                    )
                )
        elif isinstance(raw_peers, list):
            for item in raw_peers:
                if isinstance(item, dict) and item.get(b"ip"):
                    peers.append(
                        (
                            item[b"ip"].decode("utf-8", "replace"),
                            int(item.get(b"port") or 0),
                        )
                    )
        external = [peer for peer in peers if peer[0] not in own]
        return {
            "tracker": tracker.split("/")[2],
            "seeders": int(payload.get(b"complete") or 0),
            "leechers": int(payload.get(b"incomplete") or 0),
            "peers": len(peers),
            "external_peers": len(external),
            "own_peers": len(peers) - len(external),
            "own_ips": sorted(own),
        }
    return None


def parse_show_files(output):
    files = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*(\d+)\|(.+)", line)
        if not match:
            continue
        size = 0
        if index + 1 < len(lines):
            size_match = re.search(r"([\d.]+)\s*(GiB|MiB|KiB|B)", lines[index + 1])
            if size_match:
                value = float(size_match.group(1))
                unit = size_match.group(2)
                size = int(
                    value * {"GiB": GB, "MiB": 1024 ** 2, "KiB": 1024, "B": 1}[unit]
                )
        path = match.group(2).strip()
        files.append(
            {
                "index": int(match.group(1)) - 1,
                "path": path,
                "name": os.path.basename(path),
                "size": size,
            }
        )
    return files


def fetch_metadata(info_hash, timeout, deadline=None):
    """Fetch torrent metadata through aria2c and summarize the file list.

    `deadline` is an absolute time.monotonic() value (the soft budget's end).
    The subprocess hard-kill slack is clamped to it, so a stuck aria2c cannot
    outlive the round's budget by the usual `timeout + 10` seconds.
    """
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    for tracker in ANNOUNCE_TRACKERS:
        magnet += f"&tr={urllib.parse.quote(tracker, safe='')}"

    out = os.path.join("/tmp", f"{info_hash}.torrent")
    try:
        os.remove(out)
    except FileNotFoundError:
        pass

    hard_timeout = timeout + 10
    if deadline is not None:
        hard_timeout = max(min(hard_timeout, deadline - time.monotonic()), 1.0)

    try:
        subprocess.run(
            [
                "aria2c",
                ARIA2_DHT_FLAG,
                "--bt-metadata-only=true",
                "--bt-save-metadata=true",
                "--seed-time=0",
                f"--bt-stop-timeout={timeout}",
                "--summary-interval=0",
                "--disable-ipv6=true",
                "--bt-tracker-connect-timeout=5",
                "--bt-tracker-timeout=8",
                "--dir=/tmp",
                magnet,
            ],
            capture_output=True,
            timeout=hard_timeout,
            check=False,
        )
        if not os.path.exists(out):
            return None
        result = subprocess.run(
            ["aria2c", "--show-files", out],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None

    name_match = re.search(r"^Name:\s+(.+)$", result.stdout, re.MULTILINE)
    total_match = re.search(r"Total Length:\s+([\d.]+)\s*([GMK])iB", result.stdout)
    if not name_match:
        return None

    total_size = 0
    if total_match:
        value = float(total_match.group(1))
        unit = total_match.group(2)
        total_size = int(value * {"G": GB, "M": 1024 ** 2, "K": 1024}[unit])

    files = parse_show_files(result.stdout)
    videos = [
        item
        for item in files
        if os.path.splitext(item["name"])[1].lower() in VIDEO_EXTS
        and item["size"] >= MIN_VIDEO_BYTES
    ]
    junk = [
        item
        for item in files
        if os.path.splitext(item["name"])[1].lower() in JUNK_EXTS
    ]
    main_video = max(videos, key=lambda item: item["size"], default=None)
    return {
        "name": name_match.group(1).strip(),
        "size_gb": total_size / GB,
        "nfiles": len(files),
        "videos": videos,
        "main_video_gb": (main_video["size"] / GB) if main_video else 0.0,
        "main_video_ext": (
            os.path.splitext(main_video["name"])[1].lower() if main_video else ""
        ),
        "junk_files": len(junk),
        "junk_bytes": sum(item["size"] for item in junk),
    }


def pool_file_for(args):
    """Resolve the candidate-pool path used to accumulate candidates."""
    if args.no_pool:
        return ""
    if args.pool_file:
        return args.pool_file
    seed = args.query or args.candidate_file
    if not seed:
        hashes = [parse_candidate(item) or item for item in args.magnet]
        hashes.extend(args.info_hash)
        seed = "-".join(sorted(value[-20:] for value in hashes if value)) or "pool"
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", seed).strip("-")
    return os.path.join(POOL_DIR, (slug or "pool")[:60] + ".json")


def load_pool(path):
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries")
    if isinstance(entries, dict):
        return entries
    return {}


def write_json_atomic(path, payload, skip_label="JSON_WRITE_SKIPPED"):
    """Write JSON through a temp file + os.replace.

    The console reads `--json-out` right after the probe process ends, and the
    process may also be killed mid-round; a direct write can leave a truncated
    file that json.load turns into "no candidates". Rename is atomic on POSIX,
    so a reader either sees the previous file or the complete new one.
    """
    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        # A read-only pool/json directory must not stop a download.
        print(f"{skip_label} {exc}")


def save_pool(path, entries, query=""):
    if not path:
        return
    with _POOL_LOCK:
        # Snapshot under the lock: worker threads keep enriching the pool while
        # the main thread lands each finished candidate.
        snapshot = copy.deepcopy(entries)
    payload = {
        "version": 1,
        "query": query,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "entries": snapshot,
    }
    write_json_atomic(path, payload, skip_label="POOL_WRITE_SKIPPED")


def pool_metadata(pool, info_hash, now):
    """池缓存的 metadata；「有戳但清单里没有可用视频」视为未命中。

    判据与写入侧共用 `metadata_has_usable_video()`（N7 的单点定义）：`metadata_at`
    代表「文件清单已核实」，所以一份 `videos: []` / `main_video_gb <= 0` 的条目
    本来就不该带着这枚章。万一它还在（修复前写出的池文件、或别的写入者，
    t15/F-T15-2 实测真实池里曾有 66 条），这里也必须当未命中——否则这一轮会直接
    以「没有可读视频」把候选剔掉，连一次 metadata 抓取都不做（返回 None 后由
    enrich 真的去 swarm 抓）。
    """
    entry = pool.get(info_hash) or {}
    stamp = float(entry.get("metadata_at") or 0)
    if not stamp or now - stamp > METADATA_TTL_SECONDS or not entry.get("name"):
        return None
    metadata = {
        "name": entry.get("name"),
        "size_gb": entry.get("size_gb", 0.0),
        "nfiles": entry.get("nfiles", 0),
        "videos": entry.get("videos") or [],
        "main_video_gb": entry.get("main_video_gb", 0.0),
        "main_video_ext": entry.get("main_video_ext", ""),
        "junk_files": entry.get("junk_files", 0),
    }
    if not metadata_has_usable_video(metadata):
        return None
    return metadata


def update_pool_entry(pool, info_hash, **fields):
    with _POOL_LOCK:
        entry = pool.setdefault(info_hash, {})
        entry.update(fields)
        return entry


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


def gather_candidates(args):
    """读取 search_pool 写下的候选清单（每行一个磁力或 info hash）。

    只剩这一个入口：这次简化删掉了浏览器搜磁力（`--magnet` / `--info-hash`）与本地
    Pansou 两条路。L1 取源的结果由 `--candidate-file` 送进来，云盘候选由
    `--extra-candidates` 送进来。
    """
    direct = []
    if args.candidate_file:
        try:
            with open(args.candidate_file, "r", encoding="utf-8") as handle:
                direct.extend(
                    line.strip()
                    for line in handle
                    if line.strip() and not line.lstrip().startswith("#")
                )
        except OSError as exc:
            print(f"CANDIDATE_FILE_ERROR {exc}")
            return None

    extracted = []
    skipped = []
    for index, candidate in enumerate(direct):
        info_hash = parse_candidate(candidate)
        if info_hash:
            extracted.append((f"direct#{index}", info_hash))
        else:
            skipped.append(candidate)

    # 解析不出来的行要报数：删掉 `--magnet`（thunder:// 直链的唯一入口）之后，直链不再
    # 有专属提示，静默丢掉会让「文件里有几行但一条候选都没有」变得不可解释。
    if skipped:
        print(
            "CANDIDATE_LINES_SKIPPED "
            + json.dumps(
                {
                    "count": len(skipped),
                    # 只报数，不把用户文件里的行抄进日志（可能含本机路径）
                    "reason": "既不是磁力也不是 40 位 info hash 的行会被跳过",
                },
                ensure_ascii=False,
            )
        )
    return extracted


def dedupe(extracted):
    """Keep one entry per info hash, remembering which sources found it."""
    order = []
    sources = {}
    for source, info_hash in extracted:
        info_hash = info_hash.upper()
        if info_hash not in sources:
            order.append(info_hash)
            sources[info_hash] = []
        sources[info_hash].append(source)
    return order, sources


def metadata_has_usable_video(metadata):
    """这份 metadata 的文件清单里有没有可交付的视频。

    `metadata_at` 只描述一件事：**这份候选的文件清单已由 metadata 抓取核实**
    （与 `search_pool.py` 开头的字段契约是同一定义）。清单里没有 ≥100MB 的视频
    时候选不可交付，此时盖章会让 `pool_metadata` 在 7 天 TTL 内一直命中，
    下一轮连抓都不抓就直接判 "no readable video file >=100MB in torrent
    metadata"（t10/F1 的读取侧缺口）。所以写入规则与 `search_pool` 对齐：
    没有可用视频就不写 `metadata_at`。
    """
    videos = metadata.get("videos") or []
    if not videos:
        return False
    return float(metadata.get("main_video_gb") or 0) > 0


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


def _save_progress(pool_path, pool, query=""):
    """每完成一条候选就落盘一次（健康度与 metadata 两个阶段都调用）。

    原来只在全流程末尾存池子：搜索中途被杀（后端重启、超时、预算耗尽）就把已经付过
    网络代价的 metadata 全丢了。现在逐条落盘（原子写，控制台不会读到半写 JSON），
    `metadata_at` 有 7 天 TTL、`health_at` 有 1 小时 TTL，下次直接复用。
    （`measured_at` 的 6 小时 TTL 随 aria2c 测速一起删除，这里不再提它。）
    """
    if not pool_path:
        return
    try:
        save_pool(pool_path, pool, query)
    except OSError as exc:  # 落盘失败不该打断搜索
        print(f"  (进度落盘失败：{exc})", flush=True)


def is_fresh(pool, info_hash, field, ttl, now):
    stamp = float((pool.get(info_hash) or {}).get(field) or 0)
    return bool(stamp) and now - stamp <= ttl


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


def load_extra_candidates(path, policy):
    """Load cloud-drive (or other non-magnet) candidates from a JSON list."""
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"EXTRA_CANDIDATE_ERROR {exc}")
        return []
    items = payload.get("candidates") if isinstance(payload, dict) else payload
    candidates = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if structural_reject(item, policy):
            print(
                "  EXTRA_SKIP "
                + json.dumps(
                    {
                        "name": str(item.get("name"))[:60],
                        "reason": structural_reject(item, policy),
                    },
                    ensure_ascii=False,
                )
            )
            continue
        candidate = dict(item)
        if policy.get("duration_seconds") and candidate.get("main_video_gb"):
            candidate["estimated_bitrate_mbps"] = (
                candidate["main_video_gb"] * GB * 8
                / policy["duration_seconds"]
                / 1e6
            )
        else:
            candidate.setdefault("estimated_bitrate_mbps", None)
        candidates.append(candidate)
    if candidates:
        print(f"合并 {len(candidates)} 个云盘/非磁力候选进入同一轮打分")
    return candidates


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

    resources = []
    # 本机公网 IP 只在真的要 announce 时才查（它是一次 ≤5s 的 HTTP 调用，
    # 而健康度全部命中缓存时根本用不到）。预算不够就不查，让 announce 自己去失败。
    own_ips_cache = []

    def own_ips():
        if not own_ips_cache:
            if budget.out_of_time("health", MIN_HEALTH_BUDGET_SECONDS):
                return []
            own_ips_cache.append(public_ip())
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
            health = announce_health(
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
                seeds, peers = tracker_health(
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

    print(
        f"\nFetching metadata for {len(resources)} candidates "
        f"with {args.workers} workers..."
    )
    enriched = []
    rejected = []
    metadata_started = timers.start()
    metadata_deadline = budget.deadline if budget.enabled else None
    pending = []
    skipped_for_budget = []
    for resource in resources:
        if budget.remaining() < MIN_METADATA_BUDGET_SECONDS:
            # 预算不够跑一次 metadata 抓取：已缓存的候选照常提交（不花网络时间，
            # enrich 只在需要联网时才看 deadline），未缓存的跳过并记账。
            if not pool_metadata(pool, resource["ih"], now):
                budget.out_of_time("metadata", MIN_METADATA_BUDGET_SECONDS)
                skipped_for_budget.append(resource["ih"])
                continue
        pending.append(resource)
    if skipped_for_budget:
        print(
            f"  软预算用尽：{len(skipped_for_budget)} 个未缓存候选跳过 metadata 抓取"
            f"（timeout_stage={budget.timeout_stage}）"
        )
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(args.workers, 1)
    ) as executor:
        future_map = {
            executor.submit(
                enrich,
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
            _save_progress(pool_path, pool, args.query)
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

    extras = load_extra_candidates(args.extra_candidates, policy)
    if not enriched and extras:
        # 这句要等到云盘候选真的读进来了再说：原来它在读之前就打印，两个文件都读不到时
        # 会声称「仅对云盘候选打分」，而实际零候选（复核第二轮 F7）。
        print(f"没有磁力候选，仅对 {len(extras)} 个云盘/非磁力候选打分")
        enriched = extras
        extras = []
    if not enriched:
        save_pool(pool_path, pool, args.query)
        # 这一轮没有产出可交付候选：池子已经落盘（partial 状态在里面），但
        # --json-out 保持不写 —— 控制台据此保留上一份快照，退出码语义不变。
        if budget.timeout_stage or skipped_for_budget:
            print(
                "PROBE_PARTIAL "
                + json.dumps(
                    {
                        "timeout_stage": budget.timeout_stage,
                        "expired_stages": budget.expired_stages,
                        "budget_secs": budget.total_seconds,
                        "skipped_uncached_metadata": len(skipped_for_budget),
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
            if external and max(external) <= 3:
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

    enriched.extend(extras)

    ranked = rank_candidates(enriched, policy)

    live_summary = {"checked": 0, "reused_from_enrich": 0}
    if args.live_check and any(item.get("ih") for item in ranked):
        print("\n校验头部候选能否真正解析元数据（迅雷的“种子解析失败”就出在这一步）...")
        live_started = timers.start()
        live_summary = verify_live(
            ranked, pool, now, args, round_started_at=round_started_at, budget=budget
        )
        timers.done("live_check", live_started)
        completed["live_check"] = live_summary["checked"]
        # re-score：live-check 把「本轮证明可达」写进了候选字典，排名要按新事实重算。
        ranked = rank_candidates(enriched, policy)

    save_pool(pool_path, pool, args.query)

    timeout_stage = budget.timeout_stage
    partial = bool(timeout_stage or skipped_for_budget)
    if partial:
        print(
            "PROBE_PARTIAL "
            + json.dumps(
                {
                    "timeout_stage": timeout_stage,
                    "expired_stages": budget.expired_stages,
                    "budget_secs": budget.total_seconds,
                    "skipped_uncached_metadata": len(skipped_for_budget),
                    "elapsed_seconds": round(time.time() - round_started_at, 2),
                    "completed": completed,
                },
                ensure_ascii=False,
            )
        )

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


if __name__ == "__main__":
    raise SystemExit(main())
