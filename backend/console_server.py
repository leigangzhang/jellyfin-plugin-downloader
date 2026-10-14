#!/usr/bin/env python3
"""省流版本地控制台服务（Python 标准库 http.server，零第三方依赖）。

静态：/ /editor.html /app.js /console.css /vendor/*
API：/api/snapshot /api/search /api/submit /api/watch /api/cleanup
     /api/pan/<subcommand> /api/mark /api/publish /api/publish/rollback

所有写动作都委托给主 skill 脚本（subprocess）或复用其库函数（import），本服务
本身不改主 skill 的 pool 与迅雷数据库。

本文件是入口，也是契约面 + 组合根：路径被外部契约钉死（插件的 BackendManager 按
`<插件目录>/backend/console_server.py` 启动、uninstall.sh 按 `console_server.py`
认这个进程、测试按这条路径读源码做断言），所以常量、状态映射、service 信封与端口
默认值留在本文件；实现按主题拆在 `backend/console_server/` 包里（HTTP 面 / 搜索任务 /
快照 / 进程），由下面的导入与 `_wire_http_api()` 接起来 —— 包内不复制这些定量，
也不 import 入口（脚本方式运行时入口叫 `__main__`，再 import 一次会让状态分身）。
"""
from __future__ import annotations

import argparse
import atexit
import os
import signal
import sys
import threading
from http.server import ThreadingHTTPServer

import build_console as bc
import console_server.http_api as http_api

# 下面这一组导入同时是「再导出」：拆分前 `console_server.<name>` 能取到的符号，
# 入口仍然取得到（测试、以及任何按模块用的地方都照旧）。
from console_server import jobs, probe_state, service
from console_server.http_api import (
    MIME, WEB_DIR, _body_inner, render_index, valid_target_dir,
)
from console_server.jobs import (
    JOBS, JOBS_LOCK, _JOB_SEQ, _job_add, _job_finish, _new_job, episode_hints,
    season_hints,
)
from console_server.probe_state import (
    PUBLISH_HISTORY, _build_snapshot, _file_written_since, _parse_exists,
    _probe_partial, _snapshot_history,
)
from console_server.service import (
    MAIN_SCRIPTS, PID_FILE, _ACTIVE_PROCS, _PROCS_LOCK, _handle_signal, _kill_group,
    _pidfile_alive, _remove_pidfile, _shutdown_children, _track, _untrack,
    _write_pidfile, run_cli, run_cli_stream,
)

# 自身标识：插件侧的 BackendManager.Reachable() 靠它区分「我们的后端」与「恰好占着
# 这个端口的外来 HTTP 服务」。两边的字面量是同一个契约——改一处必须改另一处，
# tests/test_backend_retired_paths.py 有一条守卫盯着。
SERVICE_ID = "jellyfin-downloader-backend"

# 单轮时限口径（各阶段单位耗时来自 2026-10-13 实测，单轮总时长是按这些单位耗时推算的；
# 原始数据见 /tmp/jmd-audit/t3-speed.md、t4-pipeline.md）：
#   * probe 不再跑速度实测：10s 探针的重复散布 1.16–8.50×，不足以排序，
#     而它的固定成本是 8 条 × 20–23s ≈ 160–184s（t3-speed.md §0/§2）。
#   * 剩下的 probe 阶段：健康度 announce 0.5–0.9s/条（死 tracker 已剔除，最坏 24s 降到 3s）、
#     metadata 命中池缓存 0s / 未命中 6.2–8.4s（失败 26.6s，4 并发）、live-check 已复用本轮
#     metadata 时间戳，不再对同一 hash 抓第二次。
#   * 一轮 probe 的耗时是**推算**（T4 的单位耗时 × 阶段次数，去探针后），不是本机实测：
#     典型 ≈ 60–160s，最坏（metadata 全失败）≈ 300s。真实网络下的预算行为尚未跑过
#     （见 t10/N4）；要把它升级成「实测」，得真跑一轮并把墙钟记进注释。
# 所以 1800s 折算下来是 5–6 倍余量；收到 900s，并把 probe 自己的软预算压在其下，让它在被杀之前
# 自己收工、把 partial 结果落盘（而不是整轮白跑）。
SEARCH_TIMEOUT = 900
PROBE_BUDGET_SECS = 720  # < SEARCH_TIMEOUT - 搜索/云盘阶段/进程收尾的余量
SEARCH_POOL_TIMEOUT = 600
VERIFY_TIMEOUT = 60

# L1 搜索阶梯的墙钟上界（t10/N3 要求 ≤450s，且要小于 SEARCH_POOL_TIMEOUT 才有余量）：
#   --max-queries 12 × --page-budget-secs 36 = 432s ≤ 450s；按 search_pool 的实际口径
#   （它会把 8s 的外部渠道预留从每个关键词预算里扣掉）只有 12 × 28s + 8s ≈ 344s。
#   两个值都必须显式传：不传 --page-budget-secs 会落到 search_pool 的默认 45s，
#   12 × 45 = 540s 就贴着 600s 上限了（这正是 N3 记的那条余量不足）。
L1_MAX_QUERIES = 12
L1_KEYWORD_BUDGET_SECS = 36

# probe 的预算参数。`"--budget-secs"` 这个字面量只有入口这一个生产者（原来它内联在
# 搜索任务的 probe_cmd 里），取出来是为了让「预算常量 + 参数名」一起留在契约面上：
# 测试按文件路径读入口源码断言这两样。
PROBE_ARGV = ("--budget-secs", str(PROBE_BUDGET_SECS))


def build_search_cmd(p):
    """L1 搜索（资源站磁力）的 argv。

    阶梯上界（L1_MAX_QUERIES / L1_KEYWORD_BUDGET_SECS）只有入口这一份，所以 argv
    在入口拼；由 `_run_search_job` 作为接线传给包内的搜索任务。
    """
    search_cmd = [
        "search/search_pool.py", p["title"],
        "--pool-file", p["pool_file"],
        "--candidates-out", p["cand_file"],
        "--json-out", os.path.join("/tmp", f"{p['slug']}_search.json"),
    ]
    if p["year"]:
        search_cmd += ["--year", p["year"]]
    for a in p["alt_title"]:
        if a:
            search_cmd += ["--alt-title", a]
    if p.get("original_title") and p["original_title"] not in p["alt_title"]:
        search_cmd += ["--alt-title", p["original_title"]]
    if p["season"]:
        search_cmd += _season_cmd_args(p)
    if p.get("publisher_only"):
        search_cmd += ["--publisher-only"]
    return search_cmd


def _season_cmd_args(p):
    """季档位：季关键词 + 集级关键词（单集页排在最前）+ 阶梯上界。"""
    season_number = int(p["season"])
    # 单集页：集级关键词排在最前，先抓「只含本集」的种子
    #（strict 模式按顺序跑，整包词排在后面兜底 —— 整包也覆盖本集）
    hints = []
    if p.get("episode"):
        hints += episode_hints(season_number, int(p["episode"]))
    # 把「第 N 季」的各种写法喂给「整包关键词」档位，让搜索指向该季
    hints += season_hints(season_number)
    args = ["--season", str(season_number)]
    for hint in hints:
        args += ["--collection-hint", hint]
    if p.get("strict_season", True):
        # 严格按季：跳过基础片名/年份/平台档位，只用季关键词，
        # 避免基础片名把整部剧（其它季 + 同名片）都带进来
        args += ["--strict-match"]
    # 跑满阶梯：默认 --enough 12 会让基础片名查询提前收工。
    # 上界见上面 L1_MAX_QUERIES / L1_KEYWORD_BUDGET_SECS 的注释（≤450s）。
    args += [
        "--enough", "40",
        "--max-queries", str(L1_MAX_QUERIES),
        "--page-budget-secs", str(L1_KEYWORD_BUDGET_SECS),
    ]
    return args


def _search_status(out: str, probe: dict, rc: int) -> str:
    if probe.get("best_below_min_score"):
        return "best_below_min_score"
    if probe.get("best_dead_swarm"):
        return "best_dead_swarm"
    for token in (
        "SWARM_DEAD", "METADATA_FAILED", "PROBE_PARTIAL", "NO_ELIGIBLE_RESOURCE",
        "NO_CANDIDATES", "BEST_BELOW_MIN_SCORE", "BEST_DEAD_SWARM",
    ):
        if token in out:
            if token == "PROBE_PARTIAL" and (probe.get("ranked") or probe.get("best")):
                # 有交付候选的一轮：状态按交付结果报（ok/below/…），「本轮早退」
                # 由快照的 probe_partial 字段单独表达，不顶掉交付状态。
                continue
            return {
                "SWARM_DEAD": "swarm_dead",
                "METADATA_FAILED": "metadata_failed",
                "PROBE_PARTIAL": "probe_partial",
                "NO_ELIGIBLE_RESOURCE": "no_eligible_resource",
                "NO_CANDIDATES": "no_candidates",
                "BEST_BELOW_MIN_SCORE": "best_below_min_score",
                "BEST_DEAD_SWARM": "best_dead_swarm",
            }[token]
    if rc == 0:
        return "ok"
    return f"rc_{rc}"


def _run_search_job(jid, p):
    """后台执行一轮搜索（组合根薄壳：接线按入口全局解析，实现与状态在包里）。

    用关键字把入口解析出来的名字交给包内实现：测试用
    mock.patch.object(console_server, "run_cli" / "run_cli_stream") 替换的正是入口
    这两个名字，包内自己 import 一份会让替换失去作用。
    """
    jobs.run_search_job(
        jid, p,
        run_cli=run_cli,
        run_cli_stream=run_cli_stream,
        search_status=_search_status,
        build_search_cmd=build_search_cmd,
        probe_argv=PROBE_ARGV,
        search_timeout=SEARCH_TIMEOUT,
        search_pool_timeout=SEARCH_POOL_TIMEOUT,
    )


def _entry_valid_target_dir(value):
    """Handler 的目标目录校验：按入口全局解析，替换入口上的这个实现才生效。"""
    return valid_target_dir(value)


def _dispatch_search(jid, params):
    """把一轮搜索丢进后台线程；线程目标同样按入口全局解析（见上）。"""
    threading.Thread(target=_run_search_job, args=(jid, params), daemon=True).start()


class Handler(http_api.ConsoleHandler):
    """入口侧的 Handler：把 service 信封钉在入口（与 C# 侧的字面量契约）。"""

    def _envelope(self, obj):
        # 每个 JSON 响应都带上自身标识（只加一个 key，不改已有字段）：
        # 插件侧据此确认「这个端口上坐着的是我们的后端」。
        if isinstance(obj, dict) and "service" not in obj:
            obj = {**obj, "service": SERVICE_ID}
        return obj


def _default_port() -> int:
    """端口默认值：插件用 `JMD_PORT` 传进来（命令行里不拼端口，见 BackendManager.Start），
    手工直接起后端时仍可 `--port` 覆盖。变量缺失或不是数字就退回 8123。"""
    try:
        return int(os.environ.get("JMD_PORT") or 8123)
    except ValueError:
        return 8123


def main() -> int:
    parser = argparse.ArgumentParser(description="省流版本地控制台服务")
    parser.add_argument("--port", type=int, default=_default_port())
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    bc.ensure_dirs()
    live_pid = _pidfile_alive()
    if live_pid is not None:
        print(f"JMD 控制台已在运行（pid {live_pid}），拒绝重复启动", file=sys.stderr)
        return 2
    _write_pidfile()
    atexit.register(_remove_pidfile)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"JMD 控制台: http://{args.host}:{args.port}/  (editor: /editor.html)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _wire_http_api() -> None:
    """把 Handler 的目标目录校验与搜索调度接回入口。

    Handler 的实现在包里，但这两处必须按入口全局解析：入口是唯一持有预算常量与
    `_run_search_job` 的地方，测试替换的也是入口模块上的名字。必须在入口所有定义
    之后调用 —— 脚本方式运行时包会先被导入并按包内那一次接线，这里是最后一次、
    也是生效的那次。
    """
    http_api.valid_target_dir = _entry_valid_target_dir
    http_api.dispatch_search = _dispatch_search


_wire_http_api()


if __name__ == "__main__":
    raise SystemExit(main())
