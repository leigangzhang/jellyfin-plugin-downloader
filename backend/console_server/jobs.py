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

# 搜索任务注册表：job_id -> {done, lines, snapshot, slug, search_status, error,
#                            started_at, updated_at}
JOBS = {}
JOBS_LOCK = threading.Lock()
_JOB_SEQ = [0]

# 心跳与停滞看门狗（真机复现，2026-10-15）：
# 手动面板搜「凡人修仙传 / 集=188」后 `POST /api/search` 秒回 job_id=16，随后
# `/api/search/status?job=16` 连续 2 分钟以上返回 {"done": false, "lines": [],
# "error": ""}，子进程跑完、job 仍是空壳且永不 done —— 面板上的表现就是一直停在
# 「正在连接后端…」（`inject_04_search.js:102` 在发 POST 前就设了这个文案，只有拿到
# lines 才更新）。worker 的 except 分支没被触发（日志无 traceback），所以那条路径
# 是「线程在写下第一行之前就停住」。三道防线：
#   1. job 建立即写首行 + 心跳时间戳（客户端第一次轮询就有内容）；
#   2. supervisor 兜住 worker —— 没写终态就退出（提前 return / BaseException）一律收成
#      done + error；
#   3. 停滞看门狗：心跳 STALL_TIMEOUT_SECS 没刷新（进程还在但毫无进展）也收成终态。
# 阈值取值依据：入口一轮搜索的单一阶段上界是 SEARCH_TIMEOUT = 900s（probe 阶段），
# 而 `run_cli_stream` 逐行回调 on_line → 真在干活的阶段会持续刷新心跳；能连续
# 1200s（900 + 300 余量）一行都不写的，只可能是卡死。轮询间隔取 30s：客户端要能
# 在一分钟内收尾，又不至于频繁扫表。
STALL_TIMEOUT_SECS = 1200
WATCHDOG_INTERVAL_SECS = 30
STALL_ERROR = "搜索无进展（已超时）"
EARLY_EXIT_ERROR = "搜索线程提前退出"


def start_line(title: str, kind: str) -> str:
    """job 的第一行。含 title/kind，面板据此立刻显示「搜索开始 …」而不是空转。"""
    return f"搜索开始：{title}（kind={kind}）"


def _new_job(title: str = "", kind: str = ""):
    """建立 job 记录：写下首行与 started_at/updated_at（单调时钟）。

    `title` 给了就把首行一起写掉 —— 这发生在 `dispatch_search` **之前**，所以客户端
    拿到 job_id 后的第一次轮询就看到内容（线程还没被调度也一样）。不传 title 时保持
    旧形态（只有字段、没有行），历史调用点与既有测试照旧。
    """
    with JOBS_LOCK:
        _JOB_SEQ[0] += 1
        jid = str(_JOB_SEQ[0])
        now = time.monotonic()
        JOBS[jid] = {
            "done": False, "lines": [], "snapshot": None,
            "slug": "", "search_status": "", "error": "",
            "started_at": now, "updated_at": now,
        }
        if title:
            JOBS[jid]["lines"].append(start_line(title, kind))
        if len(JOBS) > 50:
            for old in sorted(JOBS, key=int)[:-50]:
                JOBS.pop(old, None)
        return jid


def _job_has_lines(jid) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(jid)
        return bool(job and job["lines"])


def _job_add(jid, line):
    """追一行 + 刷新心跳（updated_at 是看门狗唯一的判据）。"""
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if job is None:
            return
        job["lines"].append(line)
        job["updated_at"] = time.monotonic()
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
            "updated_at": time.monotonic(),
        })


def supervised_worker(jid, target, *args, **kwargs):
    """supervisor：包住 worker，保证 job 无论如何都收敛到终态。

    兜三种情况：worker 提前 return（没写终态）、抛 BaseException（Exception 之外的
    那些不会进 worker 的 except Exception）、以及 worker 之前的接线错误。真机那个
    「永不完成」的 job 就是这一类：线程在写下第一行之前停住，客户端只能永远等。
    """
    try:
        target(jid, *args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 —— 线程里任何异常都不许逃出去
        _job_add(jid, f"搜索异常：{exc!r}")
        _job_finish(jid, error=str(exc) or exc.__class__.__name__)
    finally:
        with JOBS_LOCK:
            job = JOBS.get(jid)
            unfinished = job is not None and not job.get("done")
        if unfinished:
            _job_add(jid, "搜索线程提前退出：未写终态，已按失败收尾")
            _job_finish(jid, error=EARLY_EXIT_ERROR)


def sweep_stalled_jobs(now=None) -> list:
    """停滞看门狗：把心跳超时的 job 收成 done + error，返回被收的 jid 列表。

    `now` 可注入（默认 time.monotonic()），测试里不必真的等 20 分钟。
    """
    now = time.monotonic() if now is None else now
    swept = []
    with JOBS_LOCK:
        stalled = [
            jid for jid, job in JOBS.items()
            if not job.get("done") and now - job.get("updated_at", now) > STALL_TIMEOUT_SECS
        ]
    for jid in stalled:
        _job_add(jid, f"搜索停滞：{STALL_TIMEOUT_SECS}s 没有进展，已按失败收尾")
        _job_finish(jid, error=STALL_ERROR)
        swept.append(jid)
    return swept


_WATCHDOG_STARTED = [False]


def ensure_watchdog():
    """幂等拉起看门狗线程（第一次派发搜索时调；测试直接调 sweep_stalled_jobs）。"""
    with JOBS_LOCK:
        if _WATCHDOG_STARTED[0]:
            return False
        _WATCHDOG_STARTED[0] = True

    def _loop():
        while True:
            time.sleep(WATCHDOG_INTERVAL_SECS)
            try:
                sweep_stalled_jobs()
            except Exception as exc:  # noqa: BLE001 —— 看门狗自己不能死
                print(f"WARNING 搜索停滞看门狗异常：{exc!r}", flush=True)

    threading.Thread(target=_loop, daemon=True).start()
    return True


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

    # worker 的第一件事：保证「搜索开始 …」这一行在（job 建立时 _new_job 通常已经
    # 写过，这里只为「先建 job、后补 worker」的路径兜底，不重复写同一行）。
    if not _job_has_lines(jid):
        log(start_line(p.get("title") or "", p.get("kind") or ""))

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
