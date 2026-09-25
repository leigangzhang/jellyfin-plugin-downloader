#!/usr/bin/env python3
"""Watch one Thunder task every 15 minutes and finalize it on completion.

The process is intentionally task-scoped. It stores state and logs under the
backend's own data root (`media_download_lib.STATE_DIR`, default
`<本目录>/state/watches`, `JMD_DATA_DIR` 可覆盖），preventing duplicate watchers
for the same info hash.
"""

import argparse
import datetime as dt
import os
import subprocess
import sys
import time

from handle_media_issues import handle_issues
from media_download_lib import (
    active_download_snapshot,
    finalize_download,
    find_task_by_hash,
    human_gb,
    pause_task_row,
    pid_is_running,
    read_watcher_state,
    task_status,
    watcher_paths,
    write_watcher_state,
)
from verify_media import verify_directory


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(message):
    print(f"[{utc_now()}] {message}", flush=True)


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
    pause_task_row(status["taskid"])
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
            "eta_scope": args.eta_scope,
        },
    )
    log(f"watcher started hash={info_hash[:12]} dir={download_dir}")

    while True:
        rows = find_task_by_hash(info_hash)
        if not rows:
            misses += 1
            log(f"task not found ({misses}/{args.max_misses})")
            if misses >= args.max_misses:
                payload = {
                    "pid": os.getpid(),
                    "status": "missing",
                    "info_hash": info_hash,
                    "download_dir": download_dir,
                    "finished_at": utc_now(),
                }
                write_watcher_state(info_hash, payload)
                return 3
            time.sleep(args.interval)
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
        write_watcher_state(info_hash, payload)

        if status["state"] == 5:
            payload.update({"status": "error", "finished_at": utc_now()})
            write_watcher_state(info_hash, payload)
            log("Thunder reported an error; files were left for inspection")
            return 5

        if status["state"] in (3, 4) and status["pct"] > 99.5:
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
                    "removed_files": len(cleanup["files"]),
                }
            )
            write_watcher_state(info_hash, payload)
            log(
                f"ETA abort cleanup removed={len(cleanup['files'])} "
                "files (only this task's local payload)"
            )
            return 4

        time.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("info_hash")
    parser.add_argument("download_dir")
    parser.add_argument("--interval", type=int, default=900)
    parser.add_argument("--final-dir", default="")
    parser.add_argument("--media-kind", choices=["auto", "movie", "tv"], default="auto")
    parser.add_argument("--series-name", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--max-misses", type=int, default=4)
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
