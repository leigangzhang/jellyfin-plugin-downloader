"""池条目的合并规则与第二通道（非结构化 API 的那些站）。

一条规则：合并永远不抹掉已知事实——空值（空列表 / 0 / None）不覆盖非空值，`metadata_at`
不被合并刷新（`merge_pool_entry` / `is_empty_value` / `merge_external_entries`）。
`collect_external_source` 在调用方预留的那个预算切片里跑一个非 API 站。
"""

import datetime as dt
import json
import os
import time
import torrents_csv_source

from search.search_pool_api import (
    torrents_csv_to_candidate,
)


# 第二阶段源（非结构化 API 的那些站）在同一个 wall-clock 预算里预留的秒数切片；
# 站点多于一个时按站均分，剩不到 3s 的整段跳过。
EXTRA_SOURCE_MIN_SECS = 3.0
DEFAULT_EXTRA_SOURCE_RESERVE_SECS = 8.0


# 写进池条目时要丢掉的候选侧字段：magnet 由池子/探针各自重建，来源标签属于候选而不是池。
POOL_MERGE_SKIP_KEYS = ("ih", "magnet", "hit_query", "source")
# 这些列表字段按并集合并，而不是整体替换。
POOL_MERGE_UNION_KEYS = ("external_sources",)
# 「补字段」白名单：swarm 读数 + 来源 + 索引时间 + 去重维度。
# 一个只知道名字、不知道文件清单的候选（每个外部条目都是这样）只能用这些字段去补，
# 不能改写已经由 L1 描述、或已经由探针核实过的标识与文件清单。
PROVENANCE_MERGE_KEYS = (
    "external_seeders",
    "external_seeders_age_days",
    "external_sources",
    "created_at",
    "release_key",
    "duplicate_hashes",
)
EXTERNAL_MERGE_KEYS = PROVENANCE_MERGE_KEYS


def merge_external_entries(candidates, entries, query=""):
    """Merge torrents-csv entries into the candidate dict without clobbering diao hits.

    A hash already present keeps its diao metadata (file list, size) and only
    gains the swarm fields; `fresh` counts the candidates this channel added on
    its own.
    """
    fresh = 0
    for entry in entries or []:
        candidate = torrents_csv_to_candidate(entry, query)
        if not candidate:
            continue
        existing = candidates.get(candidate["ih"])
        if existing is None:
            candidates[candidate["ih"]] = candidate
            fresh += 1
            continue
        merge_pool_entry(existing, candidate, allow_metadata_at=False,
                         keys=EXTERNAL_MERGE_KEYS)
    return fresh


def is_empty_value(value):
    """True for the values that must never overwrite a known non-empty one."""
    if value is None:
        return True
    if isinstance(value, (list, dict, str, tuple)):
        return len(value) == 0
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    return False


def merge_pool_entry(existing, incoming, allow_metadata_at=True, keys=None):
    """Fold a fresh candidate into an existing pool entry without erasing facts.

    Three rules, the first two from the I4 integration review (F2):

    * an empty list / `0` / `None` never overwrites a non-empty value, because
      the second channel knows nothing about file lists and used to blank out
      the `videos`/`main_video_gb`/`nfiles` a previous probe had established;
    * `metadata_at` is not refreshed by a merge (`allow_metadata_at=False` from
      the external path, and pool merges keep the stamp the probe wrote), so a
      merge cannot silently extend the metadata cache TTL of an untouched file
      list;
    * `keys` narrows the merge to an allow-list. The external channel passes its
      swarm/provenance keys only: an index that knows a name but no file list
      must not rewrite the identity of a candidate the L1 channel described in
      full.

    List-valued provenance fields (`external_sources`) are unioned instead of
    replaced.
    """
    for key, value in incoming.items():
        if key in POOL_MERGE_SKIP_KEYS or (keys is not None and key not in keys):
            continue
        if key == "metadata_at" and (not allow_metadata_at or key in existing):
            continue
        if isinstance(value, list) and key in POOL_MERGE_UNION_KEYS:
            merged = list(existing.get(key) or [])
            for item in value:
                if item not in merged:
                    merged.append(item)
            if merged:
                existing[key] = merged
            continue
        if is_empty_value(value) and not is_empty_value(existing.get(key)):
            continue
        existing[key] = value
    return existing


def collect_external_source(args, site, names, candidates, budget):
    """One non-API station, inside the slice the caller reserved for it.

    Never raises and never touches the L1 path: an unreachable station is
    reported as a status and contributes zero candidates.
    """
    kind = str(site.get("kind") or "")
    source = site.get("name") or kind
    report = {
        "source": source, "kind": kind, "status": None, "queries": 0, "entries": 0,
        "new": 0, "elapsed_secs": 0.0, "message": "",
        # Reported so the caller (and its tests) can see which slice this
        # station was actually allowed to spend, not which one it was meant to.
        "budget_secs": round(budget.budget_secs, 2) if budget is not None else None,
    }
    if kind != torrents_csv_source.SOURCE_NAME:
        report["status"] = "unknown-source"
        report["message"] = kind
        return report
    if budget is None or budget.remaining() < EXTRA_SOURCE_MIN_SECS:
        report["status"] = "budget-exhausted"
        return report
    base_url = site.get("base_url") or torrents_csv_source.DEFAULT_BASE_URL
    started = time.time()
    seen_queries = []
    for name in names:
        remaining = budget.remaining()
        if remaining < EXTRA_SOURCE_MIN_SECS:
            report["message"] = "budget exhausted before every alias was queried"
            break
        if name in seen_queries:
            continue
        seen_queries.append(name)
        result = torrents_csv_source.search(
            name,
            limit=args.extra_source_limit,
            timeout=max(EXTRA_SOURCE_MIN_SECS, min(float(args.extra_source_timeout), remaining)),
            base_url=base_url,
        )
        report["queries"] += 1
        report["status"] = result.get("status")
        report["message"] = str(result.get("message") or "")[:200]
        if result.get("status") != 200:
            # 一个查询失败就停止该通道，但保留已拿到的条目；L1 不受影响。
            break
        entries = result.get("entries") or []
        report["entries"] += len(entries)
        report["new"] += merge_external_entries(candidates, entries, name)
    report["elapsed_secs"] = round(time.time() - started, 2)
    if report["status"] is None:
        report["status"] = "skipped"
    return report


def load_pool(path):
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_pool(path, entries, query=""):
    if not path:
        return True
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
        return True
    except OSError as exc:
        print(f"POOL_WRITE_SKIPPED {exc}")
        return False
