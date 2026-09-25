#!/usr/bin/env python3
"""Build a candidate pool and rank it with one weighted score.

Flow:

    gather candidates            browser file / --magnet / info hash / Pansou
      -> tracker health          seeds and peers per info hash
      -> metadata                file list, size, resolution, bitrate
      -> score                   candidate_score.rank_candidates
      -> optional speed probe    aria2c short window for the current top-N
      -> re-score and report     best candidate + full ranked table

Nothing except structural validity and the disk-safety ceiling is filtered
out; the skill's old core rules (1080p, 4-6 Mbps, 5-10 GB, Chinese subtitles)
are weighted preferences inside the score.
"""

import argparse
import base64
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
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
from speed_probe import measure_torrent_speed

# Local Pansou instance (aggregates 网盘 + magnetic sources). Override with
# PANSOU_API when the port changes.
PANSOU_API = os.environ.get(
    "PANSOU_API", "http://127.0.0.1:64056/api/search"
)
HTTP_TRACKERS = [
    "http://tracker.opentrackr.org:1337/scrape",
    "http://explodie.org:6969/scrape",
    "http://tracker.openbittorrent.com:80/scrape",
    "http://tracker.torrent.eu.org:451/scrape",
]
ANNOUNCE_TRACKERS = [
    "http://tracker.opentrackr.org:1337/announce",
    "http://explodie.org:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "http://tracker.torrent.eu.org:451/announce",
]

METADATA_TTL_SECONDS = 7 * 24 * 3600
HEALTH_TTL_SECONDS = 3600
SPEED_TTL_SECONDS = 6 * 3600
MIN_VIDEO_BYTES = 100 * 1024 ** 2


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


def scrape_tracker(tracker_url, info_hash_bytes):
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
        raw = urllib.request.urlopen(request, timeout=5).read()
        decoded, _ = bdecode(raw)
        for info in decoded.get(b"files", {}).values():
            return (
                int(info.get(b"complete", 0)),
                int(info.get(b"incomplete", 0)),
            )
    except (OSError, ValueError, KeyError):
        return None
    return None


def tracker_health(info_hash):
    """Return (seeds, peers) from the first tracker that answers."""
    try:
        info_hash_bytes = bytes.fromhex(info_hash)
    except ValueError:
        return 0, 0
    for tracker in HTTP_TRACKERS:
        result = scrape_tracker(tracker, info_hash_bytes)
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


def announce_health(info_hash, own_ips=(), timeout=8):
    """Real swarm health from an HTTP announce, excluding our own announces.

    A scrape only reports a cached number, and once this machine announces the
    tracker also counts *our* client (it shows up under the local public IP,
    and under the VPN exit IP when the tunnel is up). Announce instead, look at
    the actual peer list, and drop any peer that is us.
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


def fetch_metadata(info_hash, timeout):
    """Fetch torrent metadata through aria2c and summarize the file list."""
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    for tracker in ANNOUNCE_TRACKERS:
        magnet += f"&tr={urllib.parse.quote(tracker, safe='')}"

    out = os.path.join("/tmp", f"{info_hash}.torrent")
    try:
        os.remove(out)
    except FileNotFoundError:
        pass

    try:
        subprocess.run(
            [
                "aria2c",
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
            timeout=timeout + 10,
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


def save_pool(path, entries, query=""):
    if not path:
        return
    payload = {
        "version": 1,
        "query": query,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "entries": entries,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        # A read-only pool directory must not stop a download.
        print(f"POOL_WRITE_SKIPPED {exc}")


def pool_metadata(pool, info_hash, now):
    entry = pool.get(info_hash) or {}
    stamp = float(entry.get("metadata_at") or 0)
    if stamp and now - stamp <= METADATA_TTL_SECONDS and entry.get("name"):
        return {
            "name": entry.get("name"),
            "size_gb": entry.get("size_gb", 0.0),
            "nfiles": entry.get("nfiles", 0),
            "videos": entry.get("videos") or [],
            "main_video_gb": entry.get("main_video_gb", 0.0),
            "main_video_ext": entry.get("main_video_ext", ""),
            "junk_files": entry.get("junk_files", 0),
        }
    return None


def update_pool_entry(pool, info_hash, **fields):
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
    """Collect candidate info hashes from every allowed source."""
    direct = list(args.magnet)
    direct.extend(f"magnet:?xt=urn:btih:{value}" for value in args.info_hash)
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
            return None, []

    extracted = []
    unsupported = []
    for index, candidate in enumerate(direct):
        info_hash = parse_candidate(candidate)
        if info_hash:
            extracted.append((f"direct#{index}", info_hash))
        elif candidate.lower().startswith("thunder://"):
            unsupported.append(candidate)

    if args.allow_pansou and args.query:
        extracted.extend(search_pansou(args.query, args.limit))
    return extracted, unsupported


def search_pansou(query, limit):
    print(
        "Searching Pansou for: "
        + json.dumps({"query": query, "limit": limit}, ensure_ascii=False)
    )
    url = f"{PANSOU_API}?kw={urllib.parse.quote(query)}&res=merge&src=all"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read())
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(f"PANSOU_ERROR {exc}")
        return []
    magnets = (
        payload.get("data", {}).get("merged_by_type", {}).get("magnet", [])[:limit]
    )
    found = []
    for index, item in enumerate(magnets):
        info_hash = parse_magnet(item.get("url", ""))
        if info_hash:
            found.append((f"pansou#{index}", info_hash))
    return found


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


def enrich(info_hash, timeout, duration_seconds, pool, now, refresh_meta):
    metadata = None if refresh_meta else pool_metadata(pool, info_hash, now)
    if metadata is None:
        metadata = fetch_metadata(info_hash, timeout)
        if metadata:
            update_pool_entry(pool, info_hash, metadata_at=now, **metadata)
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
    """每测完一条就落盘一次。

    速度实测是整轮里最慢的一段（串行、每条最多 ~25s）。原来只在全流程末尾
    存池子，搜索中途被杀（后端重启、超时）就把几分钟的实测结果全丢了；
    现在逐条落盘，`measured_at` 有 6 小时 TTL，下次直接复用。
    """
    if not pool_path:
        return
    try:
        save_pool(pool_path, pool, query)
    except OSError as exc:  # 落盘失败不该打断搜索
        print(f"  (进度落盘失败：{exc})", flush=True)


def measure_speeds(ranked, pool, now, args, pool_path=""):
    """Probe real throughput for the current leaders and fold it into the pool."""
    targets = [
        item
        for item in ranked
        if item.get("kind") != "pan"  # cloud-drive candidates have no ih / no aria2c probe
        and (
            args.refresh_speed
            or not is_fresh(pool, item["ih"], "measured_at", SPEED_TTL_SECONDS, now)
        )
    ][: max(args.speed_top, 1)]
    if not targets:
        print("Speed probe skipped: every leader has a fresh measurement")
        return
    print(f"\nMeasuring real download speed for {len(targets)} candidates...")
    for index, candidate in enumerate(targets, start=1):
        measured = measure_torrent_speed(
            candidate["ih"],
            seconds=args.speed_seconds,
            warmup=args.speed_warmup,
        )
        if measured.get("error") or measured.get("speed_bps", 0) <= 0:
            if measured.get("metadata_ok"):
                # 元数据能解析 = 种子可达。窗口内没测到有效吞吐（大文件、块大、
                # 起步慢，或迅雷另有加速通道）不足以判死种，也不该按种子数
                # 给一个偏低的假分——按「未知」中性处理。
                candidate["dead_swarm"] = False
                candidate["metadata_verified"] = True
                candidate["speed_unmeasured"] = True
                candidate["speed_probe_error"] = "no payload measured in window"
                update_pool_entry(
                    pool,
                    candidate["ih"],
                    probe_ok_at=now,
                    dead_swarm=False,
                )
                print(
                    f"  [{index}/{len(targets)}] 元数据可解析、窗口内未测到吞吐"
                    f" → 按未知中性给分  {str(candidate.get('name'))[:52]}"
                )
                _save_progress(pool_path, pool, args.query)
                continue
            # speed-probe 失败：aria2c 抓不到 metadata（提前退出/0 字节）。
            # 种子数（seeds）只证明该 hash 在索引里有过做种者，不证明当前
            # DHT/tracker 能取到 metadata——迅雷提交同样会「种子解析失败」。
            # 直接判 dead_swarm（速度子分 0.05），不再落入种子数估算分支，
            # 避免 "229 seeds → 0.95 分" 掩盖 metadata 不可达。
            candidate["dead_swarm"] = True
            candidate["metadata_verified"] = False
            candidate["speed_probe_error"] = measured.get("error", "")
            update_pool_entry(
                pool,
                candidate["ih"],
                probe_failed_at=now,
                dead_swarm=True,
            )
            print(
                f"  [{index}/{len(targets)}] METADATA_UNREACHABLE → dead-swarm  "
                f"{str(candidate.get('name'))[:60]}"
            )
            _save_progress(pool_path, pool, args.query)
            continue
        speed_mbps = measured["speed_bps"] / 1024 ** 2
        candidate["measured_speed_mbps"] = speed_mbps
        candidate["measured_speed_bps"] = measured["speed_bps"]
        candidate["speed_probe_error"] = ""
        update_pool_entry(
            pool,
            candidate["ih"],
            measured_at=now,
            measured_speed_mbps=speed_mbps,
        )
        print(
            f"  [{index}/{len(targets)}] {speed_mbps:6.2f}MB/s  "
            f"{str(candidate.get('name'))[:60]}"
        )
        _save_progress(pool_path, pool, args.query)


def is_fresh(pool, info_hash, field, ttl, now):
    stamp = float((pool.get(info_hash) or {}).get(field) or 0)
    return bool(stamp) and now - stamp <= ttl


def verify_live(ranked, pool, now, args):
    """Prove the leaders can actually produce torrent metadata right now.

    Metadata that came from a search index says nothing about whether the
    swarm still exists: a pack with index metadata and no seeders scores well
    and then fails in Thunder with "种子解析失败". Fetching metadata from the
    swarm is the only honest check, so the leaders get one before submission.
    """
    checked = 0
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
        checked += 1
        metadata = fetch_metadata(info_hash, args.timeout)
        if metadata:
            candidate["metadata_verified"] = True
            candidate["dead_swarm"] = False
            update_pool_entry(pool, info_hash, probe_ok_at=now)
            print(f"  实测可解析元数据: {str(candidate.get('name'))[:56]}")
        else:
            candidate["metadata_verified"] = False
            external = candidate.get("external_peers")
            if external:
                # announce 仍能看到外部 peer：本次拿不到元数据可能只是超时/抖动，
                # 不足以判定死种（实测这类种子在迅雷里能正常下载）。
                candidate["dead_swarm"] = False
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
        "--magnet",
        action="append",
        default=[],
        help="Browser-collected magnet URI; may be repeated",
    )
    parser.add_argument(
        "--info-hash",
        action="append",
        default=[],
        help="Browser-collected 40-character info hash; may be repeated",
    )
    parser.add_argument(
        "--candidate-file",
        help="Text file with one magnet URI or info hash per line",
    )
    parser.add_argument(
        "--allow-pansou",
        "--allow-pansou-fallback",
        dest="allow_pansou",
        action="store_true",
        help="Also merge Pansou search results into the candidate pool",
    )
    parser.add_argument("--limit", type=int, default=30, help="Pansou page size")
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
        "--speed-probe",
        action="store_true",
        help="Measure real throughput for the current leaders before choosing",
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
    parser.add_argument("--speed-seconds", type=int, default=10)
    parser.add_argument("--speed-warmup", type=int, default=3)
    parser.add_argument(
        "--speed-top",
        type=int,
        default=10,
        help="How many leaders to measure (default 10)",
    )
    parser.add_argument(
        "--refresh-speed",
        action="store_true",
        help="Re-measure even when a fresh pool measurement exists",
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

    extracted, unsupported = gather_candidates(args)
    if extracted is None:
        return 2
    if not extracted:
        if unsupported:
            print(
                "UNSUPPORTED_THUNDER_DIRECT_LINK "
                + json.dumps(
                    {
                        "count": len(unsupported),
                        "reason": (
                            "thunder:// currently supports wrapped magnet or info "
                            "hash only; direct HTTP/FTP/ed2k links must use a "
                            "dedicated direct-download flow"
                        ),
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        if not args.extra_candidates:
            print(
                "NO_CANDIDATES provide --magnet, --info-hash, --candidate-file, "
                "--extra-candidates, or --allow-pansou with a query"
            )
            return 4
        print("没有磁力候选，仅对云盘/额外候选打分")

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
    own_ips = [public_ip()]
    for info_hash in order:
        entry = pool.get(info_hash) or {}
        seeds, peers = 0, 0
        health_method = entry.get("health_method") or ""
        external_peers = None
        if is_fresh(pool, info_hash, "health_at", HEALTH_TTL_SECONDS, now):
            seeds = int(entry.get("seeds") or 0)
            peers = int(entry.get("peers") or 0)
            external_peers = entry.get("external_peers")
        else:
            health = announce_health(info_hash, own_ips)
            if health:
                # The tracker counts our own client too (once under the local
                # public IP, once under the VPN exit); those are not seeders.
                seeds = max(health["seeders"] - health["own_peers"], 0)
                peers = health["external_peers"]
                external_peers = health["external_peers"]
                health_method = "announce"
            else:
                seeds, peers = tracker_health(info_hash)
                health_method = "scrape"
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
        if is_fresh(pool, info_hash, "measured_at", SPEED_TTL_SECONDS, now):
            resource["measured_speed_mbps"] = float(
                entry.get("measured_speed_mbps") or 0.0
            )
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

    print(
        f"\nFetching metadata for {len(resources)} candidates "
        f"with {args.workers} workers..."
    )
    enriched = []
    rejected = []
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
            ): resource
            for resource in resources
        }
        for completed, future in enumerate(
            concurrent.futures.as_completed(future_map), start=1
        ):
            resource = future_map[future]
            try:
                metadata = future.result()
            except Exception as exc:  # noqa: BLE001 - report, never crash the pool
                metadata = None
                resource["probe_error"] = str(exc)
            if not metadata:
                reason = "metadata unavailable (no seeder or tracker response)"
                rejected.append(
                    {
                        "ih": resource["ih"],
                        "name": "",
                        "reason": reason,
                    }
                )
                print(
                    f"  [{completed}/{len(resources)}] SKIP {resource['ih'][:16]}... "
                    f"{reason}"
                )
                continue
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
                    f"  [{completed}/{len(resources)}] SKIP "
                    f"{resource['main_video_gb']:6.2f}GB "
                    f"{str(resource.get('name'))[:50]} :: {reason}"
                )
                continue
            enriched.append(resource)
            print(
                f"  [{completed}/{len(resources)}] KEEP "
                f"{resource['main_video_gb']:6.2f}GB "
                f"{str(resource.get('name'))[:60]}"
            )

    extras = load_extra_candidates(args.extra_candidates, policy)
    if not enriched and extras:
        enriched = extras
        extras = []
    if not enriched:
        save_pool(pool_path, pool, args.query)
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

    if args.live_check and any(item.get("ih") for item in ranked):
        print("\n校验头部候选能否真正解析元数据（迅雷的“种子解析失败”就出在这一步）...")
        verify_live(ranked, pool, now, args)
        ranked = rank_candidates(enriched, policy)

    if args.speed_probe:
        measure_speeds(ranked, pool, now, args, pool_path=pool_path)
        ranked = rank_candidates(enriched, policy)

    save_pool(pool_path, pool, args.query)

    best = ranked[0]
    output = {
        "query": args.query,
        "kind": args.kind,
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
                "measured_speed_mbps": item.get("measured_speed_mbps"),
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
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
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
            "next_step": "降级到下一级候选来源重新获取资源（L2 浏览器发现 magnet+云盘 / L3 Pansou 搜索 magnet+云盘）",
        }
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
        print(
            "BEST_DEAD_SWARM "
            + json.dumps(
                {
                    "score": best["score"],
                    "name": best.get("name", ""),
                    "ih": best.get("ih") or best.get("id"),
                    "magnet": output["ranked"][0]["magnet"],
                    "next_step": "降级到下一级候选来源重新获取资源（L2 浏览器发现 magnet+云盘 / L3 Pansou 搜索 magnet+云盘）",
                },
                ensure_ascii=False,
            )
        )
        print("BEST_RESULT_JSON " + args.json_out)
        return 8

    output["best"] = output["ranked"][0]
    output["magnet"] = output["ranked"][0]["magnet"]
    with open(args.json_out, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)

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
