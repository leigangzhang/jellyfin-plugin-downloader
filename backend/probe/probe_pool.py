"""候选池的磁盘格式与 metadata 缓存判定。

字段契约与 `search_pool.py` 对齐：`metadata_at` / `health_at` / `probe_ok_at` 的含义
只在 `metadata_has_usable_video` 与 `pool_metadata` 两处定义。
"""
import copy
import datetime as dt
import json
import os
import re
import threading

from media_download_lib import POOL_DIR
from probe.probe_candidate import parse_candidate


METADATA_TTL_SECONDS = 7 * 24 * 3600
HEALTH_TTL_SECONDS = 3600


_POOL_LOCK = threading.Lock()


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
