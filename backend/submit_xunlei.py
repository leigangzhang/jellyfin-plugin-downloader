#!/usr/bin/env python3
"""Submit a size-checked torrent to Thunder and enforce an ETA ceiling.

The script refuses resources whose selected video is outside the configured
size policy, observes real download throughput after task creation, and starts
a detached 15-minute watcher only when the measured ETA is acceptable.

Examples:
    python3 submit_xunlei.py HASH "/path/to/movie/" --kind movie
    python3 submit_xunlei.py HASH "/path/to/Season 01/" --kind episode --episode-number 1
"""

import argparse
import json
import os
import subprocess
import sys
import time

from media_download_lib import (
    GB,
    STAGING_ROOT,
    VIDEO_EXTS,
    active_download_snapshot,
    cleanup_task_files,
    delete_task_row,
    find_task_by_hash,
    find_task_by_id,
    human_gb,
    patch_download_path,
    patch_task_files,
    pause_task_row,
    probe_torrent_files,
    staging_dir_for_final,
    task_file_names,
    task_status,
    valid_video_files,
)


def kill_thunder():
    subprocess.run(
        ["osascript", "-e", 'quit app "Thunder"'],
        capture_output=True,
        check=False,
    )
    time.sleep(2)
    subprocess.run(
        ["pkill", "-9", "-f", "Thunder"],
        capture_output=True,
        check=False,
    )
    time.sleep(2)


def set_save_path(path):
    subprocess.run(
        [
            "defaults",
            "write",
            "com.xunlei.Thunder",
            "specifySavePath",
            "-string",
            path,
        ],
        check=True,
    )
    subprocess.run(
        [
            "defaults",
            "write",
            "com.xunlei.Thunder",
            "LastSavePath",
            "-string",
            path,
        ],
        check=True,
    )


def ensure_thunder_running():
    subprocess.run(["open", "-a", "Thunder"], check=False)
    time.sleep(2)


def launch_and_submit(magnet, download_dir, restart_thunder=False):
    if restart_thunder:
        kill_thunder()
    else:
        ensure_thunder_running()
    set_save_path(download_dir + os.sep)
    subprocess.run(["open", magnet], check=False)


def wait_for_task(info_hash, timeout_seconds=30):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        rows = find_task_by_hash(info_hash)
        if rows:
            return max(rows, key=lambda row: row["taskid"])
        time.sleep(2)
    return None


def observe_eta(
    taskid,
    max_hours,
    probe_seconds=180,
    interval=15,
    eta_scope="task",
):
    """Return (accepted, payload) using measured progress, not just UI speed."""
    initial = find_task_by_id(taskid)
    initial_status = task_status(initial)
    if not initial_status:
        return False, {"reason": "task disappeared"}

    started = time.time()
    start_done = initial_status["done"]
    initial_aggregate = active_download_snapshot()
    start_aggregate_done = initial_aggregate["done"]
    samples = []

    while time.time() - started < probe_seconds:
        time.sleep(interval)
        task = find_task_by_id(taskid)
        status = task_status(task)
        if not status:
            return False, {"reason": "task disappeared"}

        elapsed = max(time.time() - started, 1)
        progressed = max(status["done"] - start_done, 0)
        average_speed = progressed / elapsed
        effective_speed = max(average_speed, status["speed"])
        eta_hours = None
        if eta_scope == "aggregate":
            aggregate = active_download_snapshot()
            aggregate_progress = max(
                aggregate["done"] - start_aggregate_done,
                0,
            )
            aggregate_average = aggregate_progress / elapsed
            aggregate_speed = max(aggregate["speed"], aggregate_average)
            if aggregate["remaining"] > 0 and aggregate_speed > 0:
                eta_hours = aggregate["remaining"] / aggregate_speed / 3600
            eta_label = "TOTAL_ETA"
            sample_speed = aggregate_speed
        else:
            if status["total"] > status["done"] and effective_speed > 0:
                eta_hours = (
                    (status["total"] - status["done"]) / effective_speed / 3600
                )
            eta_label = "ETA"
            sample_speed = effective_speed
        samples.append(
            {
                "elapsed": elapsed,
                "done": status["done"],
                "speed": sample_speed,
                "eta_hours": eta_hours,
                "eta_scope": eta_scope,
                "state": status["state"],
                "pct": status["pct"],
            }
        )

        eta_text = f"{eta_hours:.2f}h" if eta_hours is not None else "unknown"
        print(
            f"    {eta_label} {eta_text}  "
            f"speed={sample_speed / 1024**2:.2f}MB/s  "
            f"progress={status['pct']:.2f}%",
            flush=True,
        )

        if status["state"] == 5:
            return False, {"reason": "Thunder reported an error", "samples": samples}
        if status["state"] in (3, 4) and status["pct"] > 99.5:
            return True, {"reason": "already complete", "samples": samples}
        if elapsed >= 60 and progressed <= 0:
            return False, {"reason": "no download progress", "samples": samples}
        if eta_hours is not None and eta_hours <= max_hours:
            return True, {
                "reason": "measured ETA is within limit",
                "eta_hours": eta_hours,
                "speed": sample_speed,
                "samples": samples,
            }

    final = samples[-1] if samples else {}
    eta_hours = final.get("eta_hours")
    if eta_hours is None:
        reason = "could not measure a stable download speed"
    else:
        reason = f"measured ETA {eta_hours:.2f}h exceeds {max_hours:.2f}h"
    return False, {"reason": reason, "eta_hours": eta_hours, "samples": samples}


def measure_task_speed(taskid, seconds=45, interval=15):
    """Measure sustained payload throughput over a fixed test window."""
    initial = task_status(find_task_by_id(taskid))
    if not initial:
        return {"info_hash": "", "speed_bps": 0, "bytes": 0}

    started = time.time()
    start_done = initial["done"]
    last_status = initial
    while time.time() - started < seconds:
        time.sleep(interval)
        status = task_status(find_task_by_id(taskid))
        if not status:
            break
        last_status = status
        if status["state"] == 5:
            break
    elapsed = max(time.time() - started, 1)
    progressed = max(last_status["done"] - start_done, 0)
    average_speed = progressed / elapsed
    return {
        "info_hash": "",
        "speed_bps": average_speed,
        "reported_speed_bps": last_status["speed"],
        "bytes": progressed,
        "seconds": elapsed,
        "state": last_status["state"],
        "progress": last_status["pct"],
    }


def verify_task_patch(taskid, download_dir, selected_indices):
    task = find_task_by_id(taskid)
    if not task:
        return False, "task disappeared after patch"
    create_param = json.loads(task.get("create_param") or "{}")
    actual_path = os.path.realpath(
        create_param.get("download_path", "").rstrip(os.sep)
    )
    expected_path = os.path.realpath(download_dir.rstrip(os.sep))
    actual_set = create_param.get("select_set")
    if actual_path != expected_path:
        return False, f"download path mismatch: {actual_path}"
    if sorted(actual_set or []) != sorted(selected_indices):
        return False, f"select_set mismatch: {actual_set}"
    bt_info = json.loads(task.get("bt_task_info") or "{}")
    selected = {
        int(sub.get("index", -1))
        for sub in bt_info.get("subtask", [])
        if int(sub.get("is_select", 0) or 0) == 1
    }
    if selected != set(selected_indices):
        return False, f"subtask selection mismatch: {sorted(selected)}"
    return True, ""


def reject_and_cleanup(
    task,
    download_dir,
    reason,
    output_label="ETA_REJECTED",
    restart_thunder=False,
):
    file_names = task_file_names(task)
    taskid = task["taskid"]
    info_hash = json.loads(task["create_param"]).get("info_hash", "")
    print(f"  Rejecting task: {reason}", flush=True)
    if restart_thunder:
        kill_thunder()
    else:
        pause_task_row(taskid)
    delete_task_row(taskid)
    cleanup = cleanup_task_files(download_dir, info_hash, file_names)
    if restart_thunder:
        subprocess.run(["open", "-a", "Thunder"], check=False)
    if output_label:
        print(
            output_label
            + " "
            + json.dumps(
                {
                    "taskid": taskid,
                    "reason": reason,
                    "removed_files": len(cleanup["files"]),
                    "removed_gb_logical": human_gb(cleanup["bytes"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


def start_watcher(
    info_hash,
    download_dir,
    interval,
    max_misses,
    eta_scope,
    final_dir="",
    media_kind="auto",
    series_name="",
    year="",
    detach=True,
):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_download.py")
    command = [
        sys.executable,
        script,
        info_hash,
        download_dir,
        "--interval",
        str(interval),
        "--max-misses",
        str(max_misses),
        "--eta-scope",
        eta_scope,
    ]
    if final_dir:
        command.extend(
            [
                "--final-dir",
                final_dir,
                "--media-kind",
                media_kind,
                "--series-name",
                series_name,
                "--year",
                str(year or ""),
            ]
        )
    if detach:
        command.append("--detach")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or result.stderr or "").strip()
    if output:
        print(f"  Watcher: {output}", flush=True)
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("info_hash")
    parser.add_argument("download_dir")
    parser.add_argument("--kind", choices=["auto", "movie", "episode"], default="auto")
    parser.add_argument(
        "--staging-root",
        default=STAGING_ROOT,
        help="Download only inside this staging root",
    )
    parser.add_argument("--final-dir")
    parser.add_argument("--series-name", default="")
    parser.add_argument("--year", default="")
    parser.add_argument(
        "--min-video-gb",
        type=float,
        default=0.0,
        help=(
            "Only reject a movie below this size when explicitly set; size is "
            "normally a scoring preference, not a gate"
        ),
    )
    parser.add_argument(
        "--max-video-gb",
        type=float,
        default=15.0,
        help="Disk-safety ceiling for one video file",
    )
    parser.add_argument("--episode-number", type=int, default=1)
    parser.add_argument(
        "--episode-match",
        default="",
        help="Select the first episode filename containing this marker, e.g. S01E02",
    )
    parser.add_argument(
        "--all-videos",
        action="store_true",
        help="Select every video file within the size limit",
    )
    parser.add_argument(
        "--trust-magnet",
        action="store_true",
        help="Allow submission when aria2c metadata lookup fails; select from Thunder's file list",
    )
    parser.add_argument(
        "--eta-max-hours",
        type=float,
        default=4.0,
        help="Reject an ETA above this value (default: 4 hours)",
    )
    parser.add_argument(
        "--eta-probe-seconds",
        type=int,
        default=180,
        help="How long to measure real progress before accepting the task",
    )
    parser.add_argument(
        "--eta-scope",
        choices=["task", "aggregate"],
        default="task",
        help="Use one task ETA or all active tasks combined",
    )
    parser.add_argument(
        "--watch-interval",
        type=int,
        default=900,
        help="Background watcher interval in seconds",
    )
    parser.add_argument("--watch-max-misses", type=int, default=4)
    parser.add_argument("--wait", action="store_true", help="Run watcher foreground")
    parser.add_argument("--no-watch", action="store_true")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Explicitly delete an existing task with the same info hash",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Measure actual Thunder speed, then delete the trial task",
    )
    parser.add_argument(
        "--probe-seconds",
        type=int,
        default=45,
        help="Fixed speed-test window for --probe-only",
    )
    parser.add_argument(
        "--restart-thunder",
        action="store_true",
        help="Legacy mode: restart Thunder during submission",
    )
    args = parser.parse_args()

    info_hash = args.info_hash.upper()
    download_dir = os.path.realpath(args.final_dir or args.download_dir)
    staging_dir = staging_dir_for_final(download_dir, args.staging_root)
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs(staging_dir, exist_ok=True)
    magnet = f"magnet:?xt=urn:btih:{info_hash}"

    print("1. Check existing tasks...")
    existing = find_task_by_hash(info_hash)
    if existing:
        task = existing[0]
        status = task_status(task)
        if args.probe_only:
            print(
                "SPEED_PROBE_JSON "
                + json.dumps(
                    {
                        "info_hash": info_hash,
                        "speed_bps": 0,
                        "error": "existing task was not modified",
                    },
                    ensure_ascii=False,
                )
            )
            return 5
        if not args.replace_existing:
            print(
                f"  EXISTING_TASK taskid={task['taskid']} state={status['state']} "
                f"progress={status['pct']:.2f}%"
            )
            if not args.no_watch:
                existing_param = json.loads(task.get("create_param") or "{}")
                existing_path = existing_param.get("download_path") or staging_dir
                return start_watcher(
                    info_hash,
                    existing_path,
                    args.watch_interval,
                    args.watch_max_misses,
                    args.eta_scope,
                    final_dir=download_dir,
                    media_kind="tv" if args.kind == "episode" else args.kind,
                    series_name=args.series_name,
                    year=args.year,
                    detach=not args.wait,
                )
            return 0
        reject_and_cleanup(
            task,
            staging_dir,
            "replace-existing requested",
            restart_thunder=args.restart_thunder,
        )

    print("2. Probe torrent files...")
    files = probe_torrent_files(info_hash)
    videos = valid_video_files(files)
    defer_selection_to_thunder = False
    if not videos and args.trust_magnet:
        defer_selection_to_thunder = True
        print(
            "  WARNING: aria2c could not read metadata; "
            "will select files after Thunder creates the task"
        )
    elif not videos:
        print("RESOURCE_REJECTED no readable video >=100MB found in torrent")
        return 3

    kind = args.kind
    if kind == "auto":
        kind = "movie" if len(videos) == 1 else "episode"

    selected_files = []
    if defer_selection_to_thunder:
        selected_files = []
    elif kind == "movie":
        selected_files = [max(videos, key=lambda item: item["size"])]
        if len(videos) > 1:
            print(
                f"  Detected {len(videos)} videos; using the largest as the movie "
                f"and skipping the others"
            )
    else:
        ordered = sorted(videos, key=lambda item: item["path"])
        if args.all_videos:
            selected_files = [
                item
                for item in ordered
                if item["size"] / GB <= args.max_video_gb
            ]
            if not selected_files:
                print("RESOURCE_REJECTED no video files are within the size limit")
                return 3
        elif args.episode_match:
            matches = [
                item
                for item in ordered
                if args.episode_match.lower() in item["path"].lower()
            ]
            if not matches:
                print(
                    "RESOURCE_REJECTED "
                    + json.dumps(
                        {
                            "reason": "episode marker not found",
                            "marker": args.episode_match,
                        },
                        ensure_ascii=False,
                    )
                )
                return 3
            selected_files = [matches[0]]
        elif args.episode_number < 1 or args.episode_number > len(ordered):
            print(
                f"RESOURCE_REJECTED episode-number {args.episode_number} is outside "
                f"the torrent range 1-{len(ordered)}"
            )
            return 3
        else:
            selected_files = [ordered[args.episode_number - 1]]

    selected_size_gb = (
        max(item["size"] for item in selected_files) / GB
        if selected_files
        else 0
    )
    selected_indices = [item["index"] for item in selected_files]
    if selected_files:
        print(
            f"  Selected {len(selected_files)} file(s), "
            f"largest={selected_size_gb:.2f}GB, indices={selected_indices}"
        )
        for item in selected_files[:5]:
            print(f"    idx={item['index']} {item['name'][:90]}")
        if len(selected_files) > 5:
            print(f"    ... and {len(selected_files) - 5} more")
    if selected_files and selected_size_gb > args.max_video_gb:
        print(
            "RESOURCE_REJECTED "
            + json.dumps(
                {
                    "reason": "single video exceeds maximum",
                    "size_gb": selected_size_gb,
                    "max_gb": args.max_video_gb,
                },
                ensure_ascii=False,
            )
        )
        return 3
    if (
        selected_files
        and kind == "movie"
        and args.min_video_gb > 0
        and selected_size_gb < args.min_video_gb
    ):
        print(
            "RESOURCE_REJECTED "
            + json.dumps(
                {
                    "reason": "movie is below preferred minimum",
                    "size_gb": selected_size_gb,
                    "min_gb": args.min_video_gb,
                },
                ensure_ascii=False,
            )
        )
        return 3

    print("3. Submit directly without interrupting current tasks...")
    launch_and_submit(
        magnet,
        staging_dir,
        restart_thunder=args.restart_thunder,
    )

    print("4. Patch task selection...")
    task = wait_for_task(info_hash)
    if not task:
        print("SUBMIT_FAILED task was not created")
        return 2
    taskid = task["taskid"]
    patch_download_path(taskid, staging_dir + os.sep)
    if defer_selection_to_thunder:
        bt_info = json.loads(task.get("bt_task_info") or "{}")
        selected_indices = []
        for sub in bt_info.get("subtask", []):
            name = sub.get("etm_file_name") or ""
            size = int(sub.get("file_size", 0) or 0)
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTS or size <= 0:
                continue
            if size / GB > args.max_video_gb:
                continue
            if (
                args.episode_match
                and args.episode_match.lower() not in name.lower()
            ):
                continue
            selected_indices.append(int(sub.get("index", -1)))
        selected_indices = sorted(index for index in selected_indices if index >= 0)
        if not selected_indices:
            reject_and_cleanup(
                task,
                staging_dir,
                "Thunder file list contained no matching video",
                restart_thunder=args.restart_thunder,
            )
            return 3
        print(
            f"  Thunder discovered {len(selected_indices)} video file(s): "
            f"{selected_indices}"
        )
    patch_task_files(taskid, selected_indices)
    print(f"  taskid={taskid} selected_set={selected_indices}")

    if args.restart_thunder:
        kill_thunder()
        subprocess.run(["open", "-a", "Thunder"], check=False)
        time.sleep(20)
    else:
        time.sleep(3)

    patch_ok, patch_reason = verify_task_patch(
        taskid,
        staging_dir,
        selected_indices,
    )
    if not patch_ok:
        latest = find_task_by_id(taskid)
        if latest:
            reject_and_cleanup(
                latest,
                staging_dir,
                f"online patch did not persist: {patch_reason}",
                restart_thunder=args.restart_thunder,
            )
        print(
            "PATCH_NOT_APPLIED "
            + json.dumps({"reason": patch_reason}, ensure_ascii=False)
        )
        return 5

    if args.probe_only:
        measurement = measure_task_speed(taskid, seconds=args.probe_seconds)
        measurement["info_hash"] = info_hash
        latest = find_task_by_id(taskid)
        if latest:
            reject_and_cleanup(
                latest,
                staging_dir,
                "probe-only trial completed",
                output_label=None,
                restart_thunder=args.restart_thunder,
            )
        print(
            "SPEED_PROBE_JSON "
            + json.dumps(measurement, ensure_ascii=False),
            flush=True,
        )
        return 0 if measurement["speed_bps"] > 0 else 4

    print(
        f"5. Observe measured ETA for up to {args.eta_probe_seconds}s "
        f"(limit {args.eta_max_hours:.2f}h)..."
    )
    accepted, payload = observe_eta(
        taskid,
        args.eta_max_hours,
        probe_seconds=args.eta_probe_seconds,
        eta_scope=args.eta_scope,
    )
    if not accepted:
        latest = find_task_by_id(taskid)
        if latest:
            reject_and_cleanup(
                latest,
                staging_dir,
                payload["reason"],
                restart_thunder=args.restart_thunder,
            )
        else:
            print(f"ETA_REJECTED {json.dumps(payload, ensure_ascii=False)}")
        return 4

    eta_text = payload.get("eta_hours")
    if eta_text is None:
        print(f"ETA_ACCEPTED reason={payload['reason']}")
    else:
        print(f"ETA_ACCEPTED eta={eta_text:.2f}h")

    if args.no_watch:
        return 0

    print("6. Start background watcher...")
    return start_watcher(
        info_hash,
        staging_dir,
        args.watch_interval,
        args.watch_max_misses,
        args.eta_scope,
        final_dir=download_dir,
        media_kind="tv" if args.kind == "episode" else args.kind,
        series_name=args.series_name,
        year=args.year,
        detach=not args.wait,
    )


if __name__ == "__main__":
    raise SystemExit(main())
