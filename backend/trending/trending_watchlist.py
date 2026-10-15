"""关注清单：热门条目「关注」后落在本地，供媒体库占位与榜单回显。

* 落盘 `media_download_lib.DATA_ROOT`（`JMD_DATA_DIR` 可覆盖）下的
  `trending/watchlist.json`；路径每次调用现算，测试只换模块级 `DATA_ROOT`。
* 写入走「临时文件 + `os.replace`」原子替换（与缓存同一套规则）。
* 按 `tmdb_id` 去重：已在清单里的 id **直接返回且不写文件** —— 重复点击是幂等的，
  而「重写一遍同样的内容」会让文件的 mtime 动，客户端与测试都靠 mtime/内容判变化。
* 文件缺失、坏 JSON 或形状不符一律当空清单：一份坏文件不该让关注功能永久失效。
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from media_download_lib import DATA_ROOT

from .trending_cache import TRENDING_DIRNAME

WATCHLIST_VERSION = 1
WATCHLIST_FILENAME = "watchlist.json"

# 客户端可能送 "tv" / "show" 这类叫法；清单里只留统一条目的两个取值。
_KIND_ALIASES = {
    "movie": "movie",
    "film": "movie",
    "series": "series",
    "tv": "series",
    "show": "series",
    "episode": "series",
}


def watchlist_path():
    return os.path.join(DATA_ROOT, TRENDING_DIRNAME, WATCHLIST_FILENAME)


def _coerce_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_kind(value):
    text = str(value or "").strip().lower()
    return _KIND_ALIASES.get(text, "")


def _write_json_atomic(path, payload):
    """同目录临时文件 + `os.replace`：任何失败都不留半截文件与 `.tmp`。"""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_watchlist():
    """返回按落盘顺序排列的条目副本，`tmdb_id` 一律归一成 int；坏文件返回空清单。"""
    try:
        with open(watchlist_path(), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    if isinstance(payload, dict):
        items = payload.get("items")
    elif isinstance(payload, list):
        items = payload
    else:
        items = None
    if not isinstance(items, list):
        return []

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tmdb_id = _coerce_int(item.get("tmdb_id"))
        if tmdb_id is None:
            continue
        normalized.append({**item, "tmdb_id": tmdb_id})
    return normalized


def watchlist_ids():
    """路由要的「已关注」集合：只回 tmdb_id，顺序与清单一致。"""
    return [item["tmdb_id"] for item in read_watchlist()]


def add_watch(entry, now=None):
    """关注一条：返回 `{"exists", "written", "item"}`。

    已关注 → `{"exists": True, "written": False}` 且**一个字节都不写**；首次关注 →
    写入后返回 `{"exists": False, "written": True}`。`tmdb_id` 缺失/不成形抛
    `ValueError`（调用方在边界上先校验，这里只兜底）。
    """
    entry = entry or {}
    tmdb_id = _coerce_int(entry.get("tmdb_id"))
    if tmdb_id is None:
        raise ValueError("tmdb_id is required")

    items = read_watchlist()
    for item in items:
        if item["tmdb_id"] == tmdb_id:
            return {"exists": True, "written": False, "item": item}

    item = {
        "tmdb_id": tmdb_id,
        "title": str(entry.get("title") or ""),
        "year": _coerce_int(entry.get("year")),
        "kind": _normalize_kind(entry.get("kind")),
        "added_at": int(time.time() if now is None else now),
    }
    items.append(item)
    _write_json_atomic(
        watchlist_path(), {"version": WATCHLIST_VERSION, "items": items}
    )
    return {"exists": False, "written": True, "item": item}
