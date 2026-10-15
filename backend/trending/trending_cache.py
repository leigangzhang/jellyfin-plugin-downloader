"""榜单缓存：TTL 命中就不触网，TMDb 不可达时回退上次结果。

* 键 = `(language, 榜单 id 列表)` —— 换语言或换榜单组合就是另一份缓存，互不覆盖。
* 落盘路径取 `media_download_lib.DATA_ROOT`（`JMD_DATA_DIR` 可覆盖）下的
  `trending/cache.json`；路径每次调用现算，测试因此可以只换模块级的 `DATA_ROOT`。
* 写入一律「临时文件 + `os.replace`」原子替换：并发读者要么看到旧整份，要么看到
  新整份，不会读到半截 JSON；失败时临时文件被清掉，不残留 `.tmp`。
* 时钟可注入（`now=`，缺省 `time.time()`）；TTL 缺省 21600 秒（6 小时）。
* **只在整批榜单都取到时才写缓存**：半截结果落盘会把「上次的好数据」换成「这次
  的坏数据」，下一轮再失败就没得回退了。
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from media_download_lib import DATA_ROOT

from .trending_source import DEFAULT_TIMEOUT, fetch_lists

CACHE_TTL_SECS = 21600
CACHE_VERSION = 1
TRENDING_DIRNAME = "trending"
CACHE_FILENAME = "cache.json"


def cache_dir():
    return os.path.join(DATA_ROOT, TRENDING_DIRNAME)


def cache_path():
    return os.path.join(cache_dir(), CACHE_FILENAME)


def cache_key(language, list_ids):
    return f"{language}|{','.join(list_ids)}"


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


def read_cache():
    """读整份缓存；文件缺失/坏 JSON/形状不符一律当空缓存（缓存坏了不该拖垮接口）。"""
    try:
        with open(cache_path(), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def _entry_age_ok(entry, clock, ttl):
    if not isinstance(entry, dict) or not entry.get("lists"):
        return False
    try:
        fetched_at = float(entry.get("fetched_at"))
    except (TypeError, ValueError):
        return False
    return (clock - fetched_at) < ttl


def load_lists(
    language,
    list_ids,
    api_key,
    opener=None,
    timeout=DEFAULT_TIMEOUT,
    ttl=CACHE_TTL_SECS,
    now=None,
):
    """取一组榜单，返回 `{"lists", "stale", "error"}`。

    1. 缓存命中且未过期 → 直接返回缓存，**不触网**（`stale=False`）；
    2. 否则触网取一遍；整批都取到 → 写缓存后返回（`stale=False`）；
    3. 有任何一个榜单失败 → 有旧缓存就回退旧缓存并置 `stale=True`，没有就返回
       这次的（空）榜单并置 `stale=True`，`error` 说明失败原因 —— 两种情况都不抛。
    """
    ids = list(list_ids)
    key = cache_key(language, ids)
    entries = read_cache()
    entry = entries.get(key)
    clock = time.time() if now is None else float(now)

    if _entry_age_ok(entry, clock, ttl):
        return {"lists": entry.get("lists") or [], "stale": False, "error": ""}

    fetched = fetch_lists(
        ids, api_key, language=language, opener=opener, timeout=timeout
    )
    failed = [item for item in fetched if int(item.get("status") or 0) != 200]
    if not failed:
        payload = {"version": CACHE_VERSION, "entries": dict(entries)}
        payload["entries"][key] = {
            "fetched_at": clock,
            "language": language,
            "list_ids": ids,
            "lists": fetched,
        }
        _write_json_atomic(cache_path(), payload)
        return {"lists": fetched, "stale": False, "error": ""}

    error = "; ".join(
        f"{item.get('id')}: {item.get('error')}" for item in failed
    )
    if isinstance(entry, dict) and entry.get("lists"):
        return {"lists": entry.get("lists") or [], "stale": True, "error": error}
    return {"lists": fetched, "stale": True, "error": error}
