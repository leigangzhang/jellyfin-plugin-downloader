"""Tracker / DHT 网络层：scrape、announce、本机公网 IP。

这一层只认 info hash 与 tracker 列表，不碰候选池与打分；probe_round 用它取健康度，
probe_metadata 用它拼磁力链接的 tracker 参数。
"""
import hashlib
import json
import socket
import struct
import time
import urllib.parse
import urllib.request


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
