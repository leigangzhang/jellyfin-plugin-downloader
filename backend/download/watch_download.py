#!/usr/bin/env python3
"""Watch one Thunder task every 15 minutes and finalize it on completion.

The process is intentionally task-scoped. It stores state and logs under the
backend's own data root (`media_download_lib.STATE_DIR`, default
`<本目录>/state/watches`, `JMD_DATA_DIR` 可覆盖），preventing duplicate watchers
for the same info hash.
"""

import os as _bootstrap_os
import sys as _bootstrap_sys

# 本脚本被 console_server 以 `python3 backend/<pkg>/xxx.py` 拉起时，sys.path[0] 是这个
# 子目录；包内绝对 import（from probe import ...）与根目录模块（media_download_lib /
# source_config）都要求 backend/ 在 sys.path 上，所以先把自己所在包的根补进去。
_BOOTSTRAP_ROOT = _bootstrap_os.path.dirname(
    _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
)
if _BOOTSTRAP_ROOT not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_ROOT)

import argparse
import datetime as dt
import os
import subprocess
import sys
import time

from download.handle_media_issues import handle_issues
from media_download_lib import (
    active_download_snapshot,
    finalize_download,
    find_task_by_hash,
    human_gb,
    pid_is_running,
    read_watcher_state,
    task_status,
    watcher_paths,
    write_watcher_state,
)
from download.verify_media import verify_directory


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(message):
    print(f"[{utc_now()}] {message}", flush=True)


# ---- 面板按钮状态机：阶段 ----------------------------------------------------
# 阶段与 reason 写进同一个 watch 状态文件（`state/watches/<IH>.json`），字段名
# `stage` / `stage_reason` / `stage_updated_at`；旧字段（status / progress / speed /
# eta_hours …）一律保留 —— 面板与其它调用方还在读它们。
STAGE_SUBMITTING = "submitting"
STAGE_MONITORING = "monitoring"
STAGE_PAUSED = "paused"
STAGE_VERIFYING = "verifying"
STAGE_DONE = "done"
STAGE_FAILED_SUBMIT = "failed_submit"
STAGE_FAILED_DOWNLOAD = "failed_download"
STAGE_FAILED_ETA = "failed_eta"
STAGE_FAILED_VERIFY = "failed_verify"

# 旧的 `status` → 新 `stage`：本次改动之前写下的 watch 文件没有 stage 字段，
# /api/stages 读旧文件时按这张表兜底，不至于凭空多出一批空阶段。
LEGACY_STATUS_TO_STAGE = {
    "running": STAGE_MONITORING,
    "submitting": STAGE_SUBMITTING,
    "paused": STAGE_PAUSED,
    "completed": STAGE_DONE,
    "completed_with_warnings": STAGE_DONE,
    "invalid": STAGE_FAILED_VERIFY,
    "missing": STAGE_FAILED_DOWNLOAD,
    "error": STAGE_FAILED_DOWNLOAD,
    "eta_aborted": STAGE_FAILED_ETA,
}


def stage_fields(stage, reason=""):
    """阶段三件套；合并进任何一次 write_watcher_state 的 payload。"""
    return {"stage": stage, "stage_reason": reason, "stage_updated_at": utc_now()}


def stage_for_watch_state(state):
    """取一个 watch 状态 dict 的阶段：新文件读 `stage`，旧文件按 `status` 映射。"""
    if not isinstance(state, dict):
        return ""
    stage = str(state.get("stage") or "").strip()
    if stage:
        return stage
    return LEGACY_STATUS_TO_STAGE.get(str(state.get("status") or ""), "")


def write_stage(info_hash, stage, reason="", **extra):
    """把阶段并进 watch 状态文件（合并，不抹掉已在跑的字段）。

    提交侧（`/api/submit` 的 submitting / failed_submit）与监控侧共用这一个入口，
    免得两处各自拼 payload、拼出两份格式。
    """
    state = read_watcher_state(info_hash)
    if not isinstance(state, dict):
        state = {}
    state.update(stage_fields(stage, reason))
    state.update(extra)
    state.setdefault("info_hash", str(info_hash).upper())
    state.setdefault("updated_at", utc_now())
    write_watcher_state(info_hash, state)
    return state


def classify_task_row(task, misses, max_misses):
    """后台监控的存活判定（纯函数：假快照即可测，不用真等 15 s）。

    返回 `(stage, reason)`；`stage` 为空表示「照常跑，不改阶段」。
      * 迅雷里查不到该 info_hash，且连续 misses ≥ max_misses → `failed_download`
        （原因「任务已从迅雷消失」：用户在客户端删了任务）；
      * 迅雷 state=2 → `paused`（用户在客户端按了暂停；可恢复，所以不停进程）；
      * 迅雷 state=5 → `failed_download`（客户端自己报错）。
    """
    if task is None:
        if misses >= max_misses:
            return STAGE_FAILED_DOWNLOAD, "任务已从迅雷消失"
        return None, ""
    status = task_status(task)
    if status is None:
        return None, ""
    if status["state"] == 2:
        return STAGE_PAUSED, "已在迅雷暂停"
    if status["state"] == 5:
        return STAGE_FAILED_DOWNLOAD, "迅雷报错（state=5）"
    return None, ""


def start_detached(args):
    state, log_path = watcher_paths(args.info_hash)
    current = read_watcher_state(args.info_hash)
    if current.get("status") == "running" and pid_is_running(current.get("pid")):
        print(
            f"WATCHER_ALREADY_RUNNING pid={current['pid']} state={state}",
            flush=True,
        )
        return 0

    command = [
        sys.executable,
        os.path.abspath(__file__),
        args.info_hash,
        args.download_dir,
        "--interval",
        str(args.interval),
        "--max-misses",
        str(args.max_misses),
        "--abort-eta-hours",
        str(args.abort_eta_hours),
        "--slow-strikes",
        str(args.slow_strikes),
        "--eta-scope",
        args.eta_scope,
        "--final-dir",
        args.final_dir or args.download_dir,
        "--media-kind",
        args.media_kind,
        "--series-name",
        args.series_name,
        "--year",
        args.year,
    ]
    with open(log_path, "ab") as output:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write_watcher_state(
        args.info_hash,
        {
            "pid": process.pid,
            "status": "running",
            "info_hash": args.info_hash,
            "download_dir": args.download_dir,
            "started_at": utc_now(),
            "log": log_path,
        },
    )
    print(f"WATCHER_STARTED pid={process.pid} state={state} log={log_path}")
    return 0


def abort_slow_task(info_hash, download_dir, status, reason):
    """放弃一条持续超 ETA 的任务：**只记录，不改迅雷的库、不动文件**。

    根因 #4（2026-10-15，见 ROOTCAUSE-4-reject-deletes-data.md）：旧实现直接
    `pause_task_row(status["taskid"])`（`UPDATE etm_task SET state = 2`），绕过客户端改库
    —— 与「插件删了数据、迅雷记录还写着有」的同一条错误路线。迅雷没有暂停/删除类
    URL scheme（`plutil -p /Applications/Thunder.app/Contents/Info.plist` 只注册
    thunder / ed2k / magnet / birdcmd / xunleiapp / thunderExtension，全是「加任务」类），
    所以这里不写库、不删文件：把判定与「任务仍在下」写进日志与 watcher 状态，
    由用户决定暂停还是丢弃。慢任务判定策略本身不变（调用方照旧按 strike 计数调用）。
    """
    print(
        f"ABORT_SLOW_TASK taskid={status.get('taskid')} "
        f"eta={status.get('eta_hours')} reason={reason} "
        "(task left running; no client DB write, no file removal)",
        flush=True,
    )
    return {"files": [], "bytes": 0, "dirs": []}


def run_watch(args):
    info_hash = args.info_hash.upper()
    download_dir = os.path.realpath(args.download_dir)
    final_dir = os.path.realpath(args.final_dir or args.download_dir)
    state_path, _ = watcher_paths(info_hash)
    misses = 0
    slow_strikes = 0
    previous_done = None
    previous_time = None
    previous_aggregate_done = None

    write_watcher_state(
        info_hash,
        {
            "pid": os.getpid(),
            "status": "running",
            "info_hash": info_hash,
            "download_dir": download_dir,
            "final_dir": final_dir,
            "started_at": utc_now(),
            "interval_seconds": args.interval,
            "poll_seconds": args.poll_seconds,
            "eta_scope": args.eta_scope,
            **stage_fields(STAGE_MONITORING),
        },
    )
    log(
        f"watcher started hash={info_hash[:12]} dir={download_dir} "
        f"poll={args.poll_seconds}s"
    )

    while True:
        rows = find_task_by_hash(info_hash)
        if not rows:
            misses += 1
            log(f"task not found ({misses}/{args.max_misses})")
            stage, reason = classify_task_row(None, misses, args.max_misses)
            if stage:
                payload = {
                    "pid": os.getpid(),
                    "status": "missing",
                    "info_hash": info_hash,
                    "download_dir": download_dir,
                    "finished_at": utc_now(),
                    **stage_fields(stage, reason),
                }
                write_watcher_state(info_hash, payload)
                log(f"stage={stage} reason={reason}")
                return 3
            time.sleep(args.poll_seconds)
            continue

        misses = 0
        task = rows[0]
        status = task_status(task)
        now = time.time()

        eta_hours = None
        measured_speed = status["speed"]
        aggregate = None
        if args.eta_scope == "aggregate":
            aggregate = active_download_snapshot()
            measured_speed = aggregate["speed"]
            if previous_aggregate_done is not None and now > previous_time:
                measured_speed = max(
                    measured_speed,
                    max(aggregate["done"] - previous_aggregate_done, 0)
                    / (now - previous_time),
                )
            if aggregate["remaining"] > 0 and measured_speed > 0:
                eta_hours = aggregate["remaining"] / measured_speed / 3600
            previous_aggregate_done = aggregate["done"]
        else:
            if status["speed"] > 0 and status["total"] > status["done"]:
                eta_hours = (
                    (status["total"] - status["done"]) / status["speed"] / 3600
                )
            if previous_done is not None and now > previous_time:
                measured_speed = max(
                    measured_speed,
                    max(status["done"] - previous_done, 0)
                    / (now - previous_time),
                )
            if measured_speed > 0 and status["total"] > status["done"]:
                eta_hours = (
                    (status["total"] - status["done"]) / measured_speed / 3600
                )

        previous_done = status["done"]
        previous_time = now
        eta_text = f"{eta_hours:.2f}h" if eta_hours is not None else "unknown"
        log(
            f"state={status['state']} progress={status['pct']:.2f}% "
            f"{human_gb(status['done']):.2f}/{human_gb(status['total']):.2f}GB "
            f"speed={measured_speed / 1024**2:.3f}MB/s eta={eta_text}"
        )

        payload = {
            "pid": os.getpid(),
            "status": "running",
            "info_hash": info_hash,
            "taskid": status["taskid"],
            "download_dir": download_dir,
            "state": status["state"],
            "progress": status["pct"],
            "done": status["done"],
            "total": status["total"],
            "speed": measured_speed,
            "eta_hours": eta_hours,
            "eta_scope": args.eta_scope,
            "aggregate": aggregate,
            "updated_at": utc_now(),
            "log": state_path.replace(".json", ".log"),
        }
        # 存活判定：暂停 / 客户端报错先落阶段。暂停**不停进程**（用户可能马上恢复，
        # 恢复后阶段自己会回到 monitoring），所以只写状态继续轮询。
        stage, reason = classify_task_row(task, 0, args.max_misses)
        if stage:
            payload.update(stage_fields(stage, reason))
            if stage == STAGE_PAUSED:
                payload["status"] = "paused"
        write_watcher_state(info_hash, payload)

        if stage == STAGE_PAUSED:
            log(f"stage={stage} reason={reason} taskid={status['taskid']}")
            time.sleep(args.poll_seconds)
            continue

        if status["state"] == 5:
            payload.update(
                {
                    "status": "error",
                    "finished_at": utc_now(),
                    **stage_fields(STAGE_FAILED_DOWNLOAD, "迅雷报错（state=5）"),
                }
            )
            write_watcher_state(info_hash, payload)
            log("Thunder reported an error; files were left for inspection")
            return 5

        if status["state"] in (3, 4) and status["pct"] > 99.5:
            payload.update(stage_fields(STAGE_VERIFYING, "下载完成，开始检测归档"))
            write_watcher_state(info_hash, payload)
            log(f"stage={STAGE_VERIFYING} (下载完成，开始检测归档)")
            result = finalize_download(
                download_dir,
                info_hash,
                min_duration_seconds=args.min_duration_seconds,
                deep_verify=False,
                final_dir=final_dir,
                media_kind=args.media_kind,
                series_name=args.series_name,
                year=args.year,
            )
            six_layer = verify_directory(
                final_dir,
                include_hard_subtitles=True,
            )
            handled_issues = handle_issues(
                final_dir,
                verification=six_layer,
                apply=True,
            )
            combined_warnings = result["warnings"] + six_layer["warnings"]
            if (
                result["verified"]
                and not result["invalid"]
                and six_layer["ok"]
            ):
                status_value = (
                    "completed_with_warnings"
                    if combined_warnings
                    else "completed"
                )
                payload.update(
                    {
                        "status": status_value,
                        "finished_at": utc_now(),
                        **stage_fields(STAGE_DONE),
                        "verified_files": [
                            item["path"] for item in result["verified"]
                        ],
                        "verification": result["verified"],
                        "subtitle_summary": [
                            {
                                "path": item["path"],
                                "subtitle_ok": item["subtitle_ok"],
                                "embedded": len(item["embedded_subtitles"]),
                                "external": len(item["external_subtitles"]),
                            }
                            for item in result["verified"]
                        ],
                        "decode_failures": [],
                        "removed_junk": len(result["removed_junk"]),
                        "moved": result["moved"],
                        "extra_files": result["extra_files"],
                        "warnings": combined_warnings,
                        "six_layer_verification": six_layer,
                        "handled_issues": handled_issues,
                    }
                )
                write_watcher_state(info_hash, payload)
                log(
                    f"download finalized verified={len(result['verified'])} "
                    f"junk_removed={len(result['removed_junk'])} "
                    f"moved={len(result['moved'])} "
                    f"warnings={len(combined_warnings)}"
                )
                for warning in combined_warnings:
                    log(f"warning: {warning}")
                return 0

            payload.update(
                {
                    "status": "invalid",
                    "finished_at": utc_now(),
                    **stage_fields(STAGE_FAILED_VERIFY, "检测/归档未通过"),
                    "verified_files": [
                        item["path"] for item in result["verified"]
                    ],
                    "invalid_files": result["invalid"],
                    "six_layer_verification": six_layer,
                }
            )
            write_watcher_state(info_hash, payload)
            log("completed task failed media validation; files were left in place")
            return 6

        if eta_hours is not None and eta_hours > args.abort_eta_hours:
            slow_strikes += 1
            log(
                f"ETA exceeds {args.abort_eta_hours:.2f}h "
                f"({slow_strikes}/{args.slow_strikes})"
            )
        else:
            slow_strikes = 0

        if slow_strikes >= args.slow_strikes:
            cleanup = abort_slow_task(
                info_hash,
                download_dir,
                status,
                f"sustained ETA above {args.abort_eta_hours:.2f}h",
            )
            payload.update(
                {
                    "status": "eta_aborted",
                    "finished_at": utc_now(),
                    **stage_fields(STAGE_FAILED_ETA, "下载超时（持续 ETA 过高）"),
                    "removed_files": len(cleanup["files"]),
                }
            )
            write_watcher_state(info_hash, payload)
            log(
                f"ETA abort: taskid={status.get('taskid')} left running "
                "(no client DB write, no files removed)"
            )
            return 4

        time.sleep(args.poll_seconds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("info_hash")
    parser.add_argument("download_dir")
    parser.add_argument("--interval", type=int, default=900)
    # 存活轮询节奏：面板要在 15–30 s 内发现「任务被暂停/被删除」，所以状态检查用
    # 短周期；重活（ffprobe 检测、归档）只在任务下完之后跑一次，不受它影响。
    parser.add_argument("--poll-seconds", type=int, default=15)
    parser.add_argument("--final-dir", default="")
    parser.add_argument("--media-kind", choices=["auto", "movie", "tv"], default="auto")
    parser.add_argument("--series-name", default="")
    parser.add_argument("--year", default="")
    # 判「任务已从迅雷消失」需要连续几次查不到：15 s × 2 = 30 s 迟滞，既压住
    # 「客户端正在写库」的瞬时误读，又满足面板 15–30 s 的翻状态要求。
    parser.add_argument("--max-misses", type=int, default=2)
    parser.add_argument("--abort-eta-hours", type=float, default=6.0)
    parser.add_argument(
        "--eta-scope",
        choices=["task", "aggregate"],
        default="task",
    )
    parser.add_argument("--slow-strikes", type=int, default=3)
    parser.add_argument("--min-duration-seconds", type=int, default=60)
    parser.add_argument("--detach", action="store_true")
    args = parser.parse_args()
    args.info_hash = args.info_hash.upper()
    args.download_dir = os.path.realpath(args.download_dir)

    if args.detach:
        return start_detached(args)
    return run_watch(args)


if __name__ == "__main__":
    raise SystemExit(main())
