"""搜索任务：job 注册表与一轮搜索的各阶段（查重 → L1 磁力 → 云盘 → probe → 快照）。

一轮搜索的预算/时限常量、`_search_status` 与 L1 的 argv（`--max-queries` /
`--page-budget-secs` / `--budget-secs`）都留在入口 `backend/console_server.py` ——
那几行被测试按文件路径读源码断言，只能有一份，所以入口在调用 `run_search_job` 时
把它们注入进来；本模块不复制任何定量。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time

import build_console as bc
import source_config

from . import probe_state

# 搜索任务注册表：job_id -> {done, lines, snapshot, slug, search_status, error}
JOBS = {}
JOBS_LOCK = threading.Lock()
_JOB_SEQ = [0]


def _new_job():
    with JOBS_LOCK:
        _JOB_SEQ[0] += 1
        jid = str(_JOB_SEQ[0])
        JOBS[jid] = {
            "done": False, "lines": [], "snapshot": None,
            "slug": "", "search_status": "", "error": "",
        }
        if len(JOBS) > 50:
            for old in sorted(JOBS, key=int)[:-50]:
                JOBS.pop(old, None)
        return jid


def _job_add(jid, line):
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if job is None:
            return
        job["lines"].append(line)
        if len(job["lines"]) > 800:
            job["lines"] = job["lines"][-500:]


def _job_finish(jid, snapshot=None, slug="", search_status="", error=""):
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if job is None:
            return
        job.update({
            "done": True, "snapshot": snapshot, "slug": slug,
            "search_status": search_status, "error": error,
        })


def season_hints(season: int) -> list:
    """把「第 N 季」的常见写法交给 search_pool 的整包关键词档位。"""
    hints = [f"第{season}季"]
    numeral = bc.CN_NUMERALS.get(season)
    if numeral:
        hints.append(f"第{numeral}季")
    hints.append(f"S{season:02d}")
    return hints


def episode_hints(season: int, episode: int) -> list:
    """把「第 N 集」的常见写法交给搜索阶梯（单集页专用）。

    没有这一档，单集页只能搜到季包：索引上单集资源的名字是
    `[第01集]` / `S01E01` 这种写法，靠「第1季」是搜不出来的。
    """
    return [f"第{episode:02d}集", f"第{episode}集", f"S{season:02d}E{episode:02d}"]


def run_check_stage(p, log, run_cli):
    """查重阶段：check_exists 的输出 → 快照里的 exists 字段。"""
    log(f"查重：{p['title']}（{p['category']}）")
    _rc, out, _err = run_cli(["download/check_exists.py", p["title"], p["category"]], 120)
    exists = probe_state._parse_exists(out)
    log("  " + (exists.get("detail") or exists.get("status") or "OK"))
    return exists


def run_magnet_stage(p, log, *, build_search_cmd, run_cli_stream, search_pool_timeout):
    """L1（资源站磁力）阶段：跑 search_pool，并判「本轮是否真写出了候选文件」。

    候选文件按轮命名（见入口的 `_api_search`），所以「读到上一轮」已经不可能；
    这里判的是更基本的一件事：want_magnet 为假、或 search_pool 早退（没有磁力入口 /
    站点配置不可用）时，它根本不会写出这个文件。
    """
    sites, _ = source_config.load_sites()
    if sites:
        log("L1 搜索（资源站：{}）…".format("、".join(s["name"] for s in sites)))
    else:
        log("L1 搜索：资源站配置为空或不可用，search_pool 会报 SOURCE_CONFIG_ERROR")
    search_cmd = build_search_cmd(p)
    started_at = time.time()
    run_cli_stream(search_cmd, log, search_pool_timeout)
    # 只有本轮真的写出过才把它喂给 probe：路径虽然按轮唯一，但文件可能压根
    # 没被写出来（没有磁力入口 / 配置不可用），那时传一个不存在的路径会让 probe
    # 以 CANDIDATE_FILE_ERROR 整轮退出，连带把云盘候选一起丢掉。
    written = probe_state._file_written_since(p["cand_file"], started_at)
    if not written:
        log("本轮没有写出候选磁力文件（没有磁力入口或配置不可用）→ 不拿上一轮的文件喂 probe")
    return written


def keep_pan_candidates(pan_cand, key):
    """保留云盘候选（含提取码），供快照富化（probe 的 ranked 输出不含 pass_code）。"""
    if not os.path.isfile(pan_cand):
        return
    try:
        shutil.copyfile(pan_cand, os.path.join(bc.SNAPSHOTS_DIR, f"{key}.pan.json"))
    except OSError:
        pass


def run_pan_stage(p, log, *, run_cli_stream):
    """云盘阶段：各资源站的「网页搜索地址」→ pan_search 的 `--page` 参数。

    没有入口时必须明说——空候选和「搜了但没链接」是两件事。
    """
    sites, _ = source_config.load_sites()
    web_urls = source_config.web_search_urls(p["title"], sites)
    if not web_urls:
        log("云盘通道没有入口：插件配置里没有任何资源站填了「网页搜索地址」（需含 {q} 占位符）")
        return ""
    pan_cand = os.path.join("/tmp", f"{p['slug']}_pan.json")
    pan_cmd = ["pan/pan_search.py", p["title"]]
    for site_name, url in web_urls:
        log(f"  网页搜索（{site_name}）")
        pan_cmd += ["--page", url]
    pan_cmd += ["--candidates-out", pan_cand]
    run_cli_stream(pan_cmd, log, 300)
    keep_pan_candidates(pan_cand, p["key"])
    return pan_cand


def read_probe_output(path, started_at, key, log):
    """读本轮 probe 写出的 JSON；没写出就返回 ({}, False) 并说明保留了上一份快照。

    `probe_out` 就是面板读的那份快照文件，而 probe 在「预算用尽 / 超时 / 无交付候选」
    时**不写**它（只把 PROBE_PARTIAL 打到 stdout）。所以用「文件是不是本轮写过的」
    决定要不要 save_probe：只看 os.path.isfile 会把上一轮的旧结果当成本轮结果回写、
    还刷新 mtime（t10/N2）。本轮没有产出时：不覆盖、不刷新上一份快照（历史留档），
    本轮的结果由快照里的 probe_round_empty / partial 字段表达，而不是拿旧候选冒充。
    """
    if not probe_state._file_written_since(path, started_at):
        log("本轮 probe 未写出新结果（超时/预算/无候选）→ 保留上一份快照")
        return {}, False
    try:
        with open(path, encoding="utf-8") as fh:
            probe = json.load(fh)
    except (OSError, json.JSONDecodeError):
        probe = {}
    bc.save_probe(key, probe)
    return probe, True


def round_context(p, *, exists, status, partial, timeout_stage, probe_written):
    """本轮状态写进 context.json（t15/F-T15-4）：否则快照里的
    probe_partial/probe_round_empty 只活在这一次 job 的内存里，任何从磁盘
    重建快照的路径（/api/snapshot、/api/submit 之后）都会得到
    probe_partial=False + 上一轮候选，与 search_status=probe_partial 自相矛盾。
    """
    return {
        "title": p["title"], "kind": p["kind"], "year": p["year"],
        "season": p.get("season"), "episode": p.get("episode"),
        "duration_minutes": p["duration"], "final_dir": p["final_dir"],
        "exists": exists, "search_status": status, "sources": p.get("sources"),
        "original_title": p.get("original_title", ""),
        "strict_season": bool(p.get("strict_season", True)),
        "probe_partial": bool(partial) or not probe_written,
        "probe_timeout_stage": timeout_stage,
        "probe_round_empty": not probe_written,
    }


def run_probe_stage(p, log, *, exists, want_magnet, want_pan, cand_written, pan_cand,
                    run_cli_stream, search_status, probe_argv, search_timeout):
    """probe 阶段：跑 probe、读回本轮结果、落 context、组装快照。返回 (状态, 快照)。"""
    log("打分 + 校验 metadata（probe）…")
    probe_cmd = [
        "probe/probe_magnets.py", p["title"],
        "--kind", p["probe_kind"],
        "--duration-minutes", str(p["duration"]),
        "--pool-file", p["pool_file"],
        "--json-out", p["probe_out"],
        *probe_argv,
    ]
    if want_magnet and cand_written:
        probe_cmd += ["--candidate-file", p["cand_file"]]
    if want_pan and pan_cand and os.path.isfile(pan_cand):
        probe_cmd += ["--extra-candidates", pan_cand]
    # 速度实测开关已整体删除（模块、参数、这个调用点）：10s 探针的重复散布
    # 1.16–8.50×（t3-speed.md §0/§2），排不动候选，却要固定付 8 条 × 20–23s。
    # metadata 可达性仍走 live-check（不传 --no-live-check 即默认开启）。
    probe_started_at = time.time()
    rc, lines = run_cli_stream(probe_cmd, log, search_timeout)

    out_text = "\n".join(lines)
    probe, probe_written = read_probe_output(p["probe_out"], probe_started_at, p["key"], log)
    partial, timeout_stage = probe_state._probe_partial(out_text)
    # probe 自己写 JSON 的那条路径把 partial/timeout_stage 落在文件里，优先信它；
    # 没写文件的那条路径只能从 stdout 的 PROBE_PARTIAL 读（见 _probe_partial）。
    partial = bool(probe.get("partial")) or partial
    timeout_stage = str(probe.get("timeout_stage") or timeout_stage)
    status = search_status(out_text, probe, rc)

    ctx = round_context(p, exists=exists, status=status, partial=partial,
                        timeout_stage=timeout_stage, probe_written=probe_written)
    bc.save_context(p["key"], ctx)
    snapshot = probe_state._build_snapshot(p["key"], probe=probe, ctx=ctx)
    log(f"完成：{len(snapshot['candidates'])} 个候选（状态 {status}）")
    return status, snapshot


def run_search_job(jid, p, *, run_cli, run_cli_stream, search_status, build_search_cmd,
                   probe_argv, search_timeout, search_pool_timeout):
    """后台执行搜索任务，逐步把进度行追加到 job，完成后写入快照。

    接线（CLI 运行器、状态映射、L1 argv、预算常量）由入口
    `backend/console_server.py` 注入：入口是唯一持有那几条被源码断言的定量与
    `_run_search_job` 的地方，包内 import 一份会让入口上的替换（测试用的
    mock.patch.object(console_server, ...)）失去作用。
    """
    def log(line):
        _job_add(jid, line)

    try:
        # probe 会直接往 state/snapshots/<slug>.probe.json 写 --json-out，
        # 目录必须先存在（独立后端首次运行时 state/ 是空的）。
        bc.ensure_dirs()
        exists = run_check_stage(p, log, run_cli)

        sources = p.get("sources", "magnet")
        want_magnet = sources in ("magnet", "both")
        want_pan = sources in ("pan", "both")

        cand_written = False
        if want_magnet:
            cand_written = run_magnet_stage(
                p, log, build_search_cmd=build_search_cmd, run_cli_stream=run_cli_stream,
                search_pool_timeout=search_pool_timeout)
        pan_cand = ""
        if want_pan:
            pan_cand = run_pan_stage(p, log, run_cli_stream=run_cli_stream)
        status, snapshot = run_probe_stage(
            p, log, exists=exists, want_magnet=want_magnet, want_pan=want_pan,
            cand_written=cand_written, pan_cand=pan_cand, run_cli_stream=run_cli_stream,
            search_status=search_status, probe_argv=probe_argv,
            search_timeout=search_timeout)
        _job_finish(jid, snapshot=snapshot, slug=p["slug"], search_status=status)
    except Exception as exc:  # noqa: BLE001
        log(f"搜索异常：{exc}")
        _job_finish(jid, error=str(exc))
