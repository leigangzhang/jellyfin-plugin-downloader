"""落盘与收尾：候选磁力清单、分集文件、进度落盘、池合并。

`land_progress` 是「中途被杀不白跑」的那一处：每完成一个关键词落一次盘；`collect` 的
try/finally 与 `main` 的 finally 都调它。
"""

import os

from search_pool_pool import (
    POOL_MERGE_SKIP_KEYS,
    PROVENANCE_MERGE_KEYS,
    merge_pool_entry,
    save_pool,
)
from search_pool_release import (
    candidate_markers,
    release_key,
)


def write_episode_files(directory, candidates, season=1):
    """Split candidates into `<dir>/S01E15.magnets` files for series_queue."""
    if not directory:
        return {}
    per_marker = {}
    for candidate in candidates.values():
        for marker in candidate_markers(candidate, season):
            per_marker.setdefault(marker, []).append(candidate)
    written = {}
    try:
        os.makedirs(directory, exist_ok=True)
        for marker, items in per_marker.items():
            path = os.path.join(directory, f"{marker}.magnets")
            lines = [f"# {marker} search results"]
            for item in items:
                lines.append(item["magnet"])
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            written[marker] = len(items)
    except OSError as exc:
        print(f"EPISODE_FILES_SKIPPED {exc}")
    return written


def write_candidates_file(path, candidates, reports):
    """Write every candidate magnet, grouped by the query that found it.

    F3: the groups used to be exactly the L1 `reports` queries, so any candidate
    whose `hit_query` was not among them -- every extra-source candidate, which
    is queried by bare title/alias -- silently vanished from the file. The probe
    reads candidates **only** from this file, so those magnets were lost even
    though `SEARCH_RESULT` counted them. Orphan groups are now written too, so
    the number of magnet lines equals the number of candidates.

    写入走 tmp + `os.replace`（与 `save_pool` 同一手法）：这个文件会被渐进落盘
    反复重写，而 probe 只从它读候选，半写的文件等于一整轮候选消失。返回是否写入成功。
    """
    if not path:
        return False
    lines = ["# search results (diao.im + extra sources)", "# 每行一个 magnet；# 为注释"]
    by_query = {}
    for candidate in candidates.values():
        by_query.setdefault(candidate.get("hit_query", ""), []).append(candidate)
    reported = {report["query"] for report in reports}
    for report in reports:
        query = report["query"]
        items = by_query.get(query) or []
        lines.append(f"# query: {query} (hits={report['hits']})")
        for item in items:
            lines.append(item["magnet"])
    for query in by_query:
        if query in reported:
            continue
        items = by_query[query] or []
        lines.append(f"# extra-source query: {query or '(none)'} (n={len(items)})")
        for item in items:
            lines.append(item["magnet"])
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print(f"CANDIDATE_WRITE_SKIPPED {exc}")
        return False


def stamp_candidates(candidates, now):
    """给本轮候选补上去重维度，并按 F1 规则决定要不要盖 `metadata_at`。

    `metadata_at` 只代表「文件清单已核实」：外部通道的条目 `videos` 为空，不盖这个
    章，probe 才会真的去 swarm 抓 metadata，而不是把空清单当命中的缓存再以
    「没有可读视频」剔掉。渐进落盘前也要先跑一遍，否则被杀时落进池子的条目会
    缺这枚章（下一轮要白抓一遍 metadata）。
    """
    for candidate in candidates.values():
        if candidate.get("videos"):
            candidate["metadata_at"] = now
        # 外部通道直接插进来的候选要补上同样的去重维度（否则 release_groups 会把
        # 它们算成一堆 None）。
        candidate.setdefault("release_key", release_key(candidate))
        candidate.setdefault("duplicate_hashes", [])


def land_progress(args, pool, candidates, reports, now=None):
    """把「已经拿到的那部分」原子落盘一次：池 + 候选磁力。

    为什么需要它（t15/N3）：L1 阶梯的最坏墙钟是
    `--max-queries × --page-budget-secs`（控制台传 12 × 36s = 432s），而控制台给
    这个阶段的硬超时是 `SEARCH_POOL_TIMEOUT = 600s`；中途被杀（rc=124）或抛
    KeyboardInterrupt 时，如果只在 `main()` 末尾落盘，已经翻出来的候选整段丢
    （t15 的零联网复现：池文件与候选文件都不存在）。所以每完成一个关键词就落一次。

    成本是几十 KB 的顺序写 + 一次 `os.replace`，相对每个关键词的网络耗时可以忽略。
    失败只打印不抛出：落盘不该把搜索打断（`save_pool` / `write_candidates_file`
    各自已经吞掉 OSError）。

    `now` 非空时先 `stamp_candidates()`（渐进落盘的调用方传 collect 的 `now`）。
    返回 (pool_saved, candidates_written)，便于调用方/测试核对。
    """
    if now is not None:
        stamp_candidates(candidates, now)
    merged = merge_candidates_into_pool(pool, candidates)
    pool_saved = False
    if not getattr(args, "no_pool", False):
        pool_saved = save_pool(args.pool_file, merged, args.title)
    written = write_candidates_file(args.candidates_out, candidates, reports)
    return pool_saved, written


def merge_candidates_into_pool(pool, candidates):
    """把本轮候选合并进池子条目；空值不覆盖已知事实（F2 的规则）。

    `main()` 与渐进落盘共用这一份实现，避免两处各自实现一套合并规则。
    """
    merged = dict(pool)
    for candidate in candidates.values():
        fields = {
            key: value
            for key, value in candidate.items()
            if key not in POOL_MERGE_SKIP_KEYS
        }
        entry = merged.get(candidate["ih"])
        if entry is None:
            # 新条目：candidate 自己就是它已知的全部（有文件清单才带 metadata_at）。
            merged[candidate["ih"]] = dict(fields)
            continue
        # F2：已有条目只被「补充」。空值不覆盖已知文件清单，metadata_at 不刷新；
        # 若池里那份文件清单已经核实过，而这次来的候选根本没有文件清单，则只补
        # swarm/来源字段——一个只知道名字的索引不该改写已核实的标识。
        verified_list = bool(entry.get("videos")) and bool(entry.get("metadata_at"))
        if verified_list and not fields.get("videos"):
            merge_pool_entry(entry, fields, allow_metadata_at=False,
                             keys=PROVENANCE_MERGE_KEYS)
        else:
            merge_pool_entry(entry, fields, allow_metadata_at=False)
    return merged
