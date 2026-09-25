#!/usr/bin/env python3
"""Drive a Xunlei cloud-drive resource from a share link to the local library.

The web route (chosen over app-UI and raw-API automation):

    1. open https://pan.xunlei.com/s/<shareId>?pwd=<code>
    2. 保存到我的云盘 -> pick a folder
    3. open the drive page and select the files
    4. 下载到本地 (the Thunder client turns this into a `cloud/getback` task)

Steps 1-4 are clicks in a signed-in browser, so an agent performs them; this
script owns everything around them: the step plan with exact URLs, the
verification that a transfer really landed, the discovery of the resulting
retrieve tasks, and the final validate-and-archive pass.

Subcommands:

    plan             build the step list for a candidate
    verify-transfer  confirm the share really landed in the account
    verify-retrieve  list the resulting cloud/getback tasks
    watch            wait for them, then validate and move into the library
    status           show recorded state
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

from media_download_lib import (
    GB,
    PAN_STATE_DIR,
    STAGING_ROOT,
    cloud_task_status,
    finalize_download,
    find_task_by_id,
    find_tasks_by_file_name,
    pid_is_running,
    staging_dir_for_final,
    task_status,
)
from pan_pool import cloud_candidates, parse_share_text, read_cloud_index
from submit_xunlei import set_save_path

DEFAULT_DRIVE_PAGE = "https://pan.xunlei.com/yc/"
DEFAULT_CLOUD_FOLDER = "/Jellyfin"
MIN_VIDEO_BYTES = 100 * 1024 ** 2


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(message):
    print(f"[{utc_now()}] {message}", flush=True)


def slugify(value, fallback="pan"):
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value or "").strip("-")
    return (slug or fallback)[:60]


def state_path(title):
    return os.path.join(PAN_STATE_DIR, slugify(title) + ".json")


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload["updated_at"] = utc_now()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def build_steps(state, args):
    share_url = state.get("share_url") or ""
    pass_code = state.get("pass_code") or ""
    open_url = share_url
    if pass_code and "pwd=" not in share_url:
        open_url = f"{share_url}?pwd={pass_code}"
    staging = staging_dir_for_final(
        state.get("final_dir") or "", args.staging_root
    )
    script = "python3 scripts/pan_transfer.py"
    return [
        {
            "id": "open_share",
            "actor": "browser",
            "detail": "打开分享页；未登录先扫码登录",
            "url": open_url,
            "pass_code": pass_code,
        },
        {
            "id": "save_to_drive",
            "actor": "browser",
            "detail": "点「保存到我的云盘」，目录选择 " + args.cloud_folder,
            "cloud_folder": args.cloud_folder,
        },
        {
            "id": "verify_transfer",
            "actor": "script",
            "detail": "确认文件已落到云盘（读本机缓存）",
            "command": f"{script} verify-transfer --title \"{state['title']}\"",
        },
        {
            "id": "open_drive",
            "actor": "browser",
            "detail": "打开云盘页，进入已转存目录",
            "url": DEFAULT_DRIVE_PAGE,
            "match": state.get("match") or state["title"],
        },
        {
            "id": "set_save_path",
            "actor": "script",
            "detail": f"把迅雷默认保存路径指到 staging：{staging}",
            "command": f"{script} set-save-path --title \"{state['title']}\"",
        },
        {
            "id": "download_to_local",
            "actor": "browser",
            "detail": "勾选要取的集/文件后点「下载到本地」",
            "save_path": staging,
        },
        {
            "id": "verify_retrieve",
            "actor": "script",
            "detail": "列出 cloud/getback 任务确认取回已开始",
            "command": f"{script} verify-retrieve --title \"{state['title']}\"",
        },
        {
            "id": "watch",
            "actor": "script",
            "detail": "等待取回完成 -> 六层验证 -> 归档",
            "command": f"{script} watch --title \"{state['title']}\" --detach",
        },
    ]


def command_plan(args):
    candidates = []
    if args.candidate_file:
        try:
            with open(args.candidate_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"CANDIDATE_FILE_ERROR {exc}")
            return 2
        items = payload.get("candidates") if isinstance(payload, dict) else payload
        candidates = [item for item in (items or []) if isinstance(item, dict)]
        if not candidates and isinstance(payload, dict) and payload.get("best"):
            candidates = [payload["best"]]
    if args.share_url:
        parsed = parse_share_text(args.share_url) or [
            {"share_url": args.share_url, "pass_code": args.pass_code}
        ]
        for item in parsed:
            candidates.append({"name": args.title, **item})

    pan = [
        item
        for item in candidates
        if item.get("share_url")
        or str(item.get("kind") or "") == "pan"
    ]
    if not pan:
        print(
            "NO_PAN_CANDIDATE 需要 --share-url 或 --candidate-file（probe/pan_pool 的 JSON）"
        )
        return 4
    best = pan[0]

    path = args.state or state_path(args.title)
    state = load_state(path)
    state.update(
        {
            "title": args.title,
            "match": args.match or best.get("name") or args.title,
            "share_url": best.get("share_url", ""),
            "pass_code": best.get("pass_code", ""),
            "candidate_id": best.get("id") or best.get("ih") or "",
            "score": best.get("score"),
            "final_dir": args.final_dir or best.get("final_dir", ""),
            "media_kind": args.media_kind,
            "series_name": args.series_name or args.title,
            "year": args.year,
            "cloud_folder": args.cloud_folder,
            "season": args.season,
            "created_at": state.get("created_at") or utc_now(),
            "status": state.get("status") or "planned",
            "steps": [],
            "history": state.get("history") or [],
        }
    )
    state["staging_dir"] = staging_dir_for_final(
        state["final_dir"] or "", args.staging_root
    )
    state["steps"] = build_steps(state, args)
    if best.get("in_cloud"):
        # Already in the account: nothing to transfer, start at the drive page.
        skip = {"open_share", "save_to_drive", "verify_transfer"}
        state["steps"] = [
            step for step in state["steps"] if step["id"] not in skip
        ]
        state["status"] = "transferred"
        state["in_cloud"] = True
    elif not state["share_url"]:
        print(
            "PAN_PLAN_WARNING 候选没有 share_url（且不在云盘里），"
            "第 1 步没法打开分享页；请用 --share-url 或 pan_pool 带链接的候选"
        )
    save_state(path, state)

    print(
        "PAN_PLAN "
        + json.dumps(
            {
                "title": state["title"],
                "share_url": state["share_url"],
                "pass_code": state["pass_code"],
                "score": state["score"],
                "cloud_folder": state["cloud_folder"],
                "staging_dir": state["staging_dir"],
                "final_dir": state["final_dir"],
                "state": path,
            },
            ensure_ascii=False,
        )
    )
    for index, step in enumerate(state["steps"], start=1):
        print(f"  {index}. [{step['actor']}] {step['id']}: {step['detail']}")
        if step.get("url"):
            print(f"      url: {step['url']}")
        if step.get("command"):
            print(f"      run: {step['command']}")
    return 0


def command_verify_transfer(args):
    path = args.state or state_path(args.title)
    state = load_state(path)
    if not state:
        print("PAN_STATE_MISSING 先跑 plan")
        return 2
    rows = read_cloud_index(args.cloud_db)
    candidates = cloud_candidates(rows, state.get("match") or state["title"])
    # The transfer is what reveals the real file list, so this is the first
    # honest score: name-based numbers stay provisional until here.
    from candidate_score import rank_candidates

    if candidates:
        rank_candidates(
            candidates,
            {
                "kind": "episode",
                "duration_seconds": float(state.get("episode_minutes") or 45) * 60,
                "hard_max_gb": args.max_gb,
            },
        )
    videos = sum(len(item.get("videos") or []) for item in candidates)
    total_gb = sum(item.get("size_gb") or 0 for item in candidates)
    ok = bool(candidates) and videos > 0
    state.setdefault("history", []).append(
        {
            "at": utc_now(),
            "step": "verify_transfer",
            "ok": ok,
            "candidates": len(candidates),
            "videos": videos,
            "total_gb": round(total_gb, 3),
        }
    )
    if candidates:
        state["cloud_ranking"] = [
            {
                "name": item.get("name"),
                "score": item.get("score"),
                "main_video_gb": item.get("main_video_gb"),
                "resolution": item.get("resolution"),
                "has_chs": item.get("has_chs"),
                "videos": len(item.get("videos") or []),
                "provisional": False,
            }
            for item in candidates
        ]
        print("真实文件清单打分（终判，非名称粗排）:")
        for index, item in enumerate(candidates, start=1):
            print(
                f"  {index}. {item.get('score'):.2f} "
                f"{float(item.get('main_video_gb') or 0):6.2f}GB "
                f"{len(item.get('videos') or []):>3}集 "
                f"{'CHS' if item.get('has_chs') else '---'} "
                f"{str(item.get('name'))[:52]}"
            )
    if ok:
        state["status"] = "transferred"
        state["transfer_verified_at"] = utc_now()
        state["cloud_files"] = [
            {
                "name": item["name"],
                "size_gb": item.get("size_gb"),
                "videos": len(item.get("videos") or []),
            }
            for item in candidates
        ]
    save_state(path, state)
    print(
        "TRANSFER_VERIFY "
        + json.dumps(
            {
                "ok": ok,
                "matched": len(candidates),
                "videos": videos,
                "total_gb": round(total_gb, 3),
                "cloud_rows": len(rows),
                "state": path,
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 4


def command_set_save_path(args):
    path = args.state or state_path(args.title)
    state = load_state(path)
    staging = state.get("staging_dir") or staging_dir_for_final(
        state.get("final_dir") or args.final_dir or "", args.staging_root
    )
    os.makedirs(staging, exist_ok=True)
    set_save_path(staging + os.sep)
    if state:
        state["staging_dir"] = staging
        save_state(path, state)
    print(
        "SAVE_PATH_SET "
        + json.dumps({"staging_dir": staging, "thunder_default": staging}, ensure_ascii=False)
    )
    return 0


def collect_retrieve_tasks(state, extra_pattern=""):
    pattern = state.get("match") or state.get("title") or ""
    rows = find_tasks_by_file_name(pattern) if pattern else []
    if not rows and state.get("cloud_files"):
        for item in state["cloud_files"]:
            rows.extend(find_tasks_by_file_name(item["name"]))
    if not rows and extra_pattern:
        rows = find_tasks_by_file_name(extra_pattern)
    if not rows and state.get("retrieve_tasks"):
        # The retrieve was already identified once; track those task ids
        # directly so a share title that does not appear in the file names
        # (e.g. folder "R R and M" vs file "Rick.and.Morty.S09E01...") still
        # keeps being monitored.
        for known in state["retrieve_tasks"]:
            row = find_task_by_id(known.get("taskid"))
            if row:
                rows.append(row)
    dedup = {}
    for row in rows:
        dedup[row["taskid"]] = row
    return list(dedup.values())


def describe_task(row):
    param = json.loads(row.get("create_param") or "{}")
    status = cloud_task_status(row) or task_status(row) or {}
    return {
        "taskid": row["taskid"],
        "file_name": param.get("file_name", ""),
        "download_path": param.get("download_path", ""),
        "file_size_gb": round(float(param.get("file_size") or 0) / GB, 3),
        "source": param.get("source", ""),
        "entryid": param.get("entryid", ""),
        "state": status.get("state"),
        "pct": round(status.get("pct", 0.0), 2),
        "speed_mbps": round((status.get("speed") or 0) / 1024 ** 2, 2),
        "done_gb": round((status.get("done") or 0) / GB, 3),
        "total_gb": round(
            (status.get("total") or status.get("expected") or 0) / GB, 3
        ),
        "on_disk": status.get("on_disk"),
        "path": status.get("found_path", ""),
        "complete": bool(status.get("complete")),
        "already_moved": bool(status.get("already_moved")),
    }


def command_verify_retrieve(args):
    path = args.state or state_path(args.title)
    state = load_state(path)
    rows = collect_retrieve_tasks(state, args.match)
    described = [describe_task(row) for row in rows]
    running = [
        item
        for item in described
        if not item["complete"] and not item["already_moved"]
    ]
    complete = [
        item for item in described if item["complete"] or item["already_moved"]
    ]
    if state:
        state.setdefault("history", []).append(
            {
                "at": utc_now(),
                "step": "verify_retrieve",
                "tasks": len(described),
                "running": len(running),
                "complete": len(complete),
            }
        )
        if described:
            state["status"] = "retrieving"
            state["retrieve_tasks"] = described
            state["retrieve_match"] = args.match or state.get("match", "")
        save_state(path, state)
    print(
        "RETRIEVE_VERIFY "
        + json.dumps(
            {
                "tasks": described,
                "running": len(running),
                "complete": len(complete),
                "state": path,
            },
            ensure_ascii=False,
        )
    )
    if not described:
        print(
            "NO_RETRIEVE_TASK 云盘里点「下载到本地」后迅雷才会生成 cloud/getback 任务"
        )
        return 4
    return 0


def wait_and_finalize(state, path, interval, max_misses, timeout_hours):
    deadline = time.time() + timeout_hours * 3600
    misses = 0
    download_dir = state.get("staging_dir") or state.get("final_dir")
    while time.time() < deadline:
        rows = collect_retrieve_tasks(state)
        if not rows:
            misses += 1
            log(f"no retrieve task yet (miss {misses}/{max_misses})")
            if misses >= max_misses:
                state = load_state(path) or state
                state["status"] = "missing"
                save_state(path, state)
                return 3
            time.sleep(interval)
            continue
        misses = 0
        described = [describe_task(row) for row in rows]
        pending = [
            item
            for item in described
            if not item["complete"] and not item["already_moved"]
        ]
        log(
            f"{len(described)} retrieve task(s), {len(pending)} pending, "
            f"done={sum(item['done_gb'] for item in described):.2f}GB"
        )
        if not pending:
            if not any(item["on_disk"] for item in described):
                state = load_state(path) or state
                state["status"] = "already_moved"
                save_state(path, state)
                log("all retrieve tasks finished earlier; files are no longer in place")
                return 6
            break
        time.sleep(interval)
    else:
        state = load_state(path) or state
        state["status"] = "eta_aborted"
        save_state(path, state)
        return 4

    result = finalize_download(
        download_dir,
        "",
        min_duration_seconds=60,
        final_dir=state.get("final_dir") or download_dir,
        media_kind=state.get("media_kind", "auto"),
        series_name=state.get("series_name", ""),
        year=state.get("year", ""),
    )
    state = load_state(path) or state
    state["status"] = "completed" if result["verified"] and not result["invalid"] else "invalid"
    state["finalize"] = {
        "verified": len(result["verified"]),
        "invalid": len(result["invalid"]),
        "moved": result["moved"],
        "warnings": result["warnings"],
    }
    state.setdefault("history", []).append(
        {"at": utc_now(), "step": "finalize", "status": state["status"]}
    )
    save_state(path, state)
    log(f"finalize status={state['status']}")
    return 0 if state["status"] == "completed" else 5


def command_watch(args):
    path = args.state or state_path(args.title)
    state = load_state(path)
    if not state:
        print("PAN_STATE_MISSING 先跑 plan")
        return 2
    if args.detach:
        command = [
            sys.executable,
            os.path.abspath(__file__),
            "watch",
            "--title",
            state["title"],
            "--state",
            path,
            "--interval",
            str(args.interval),
            "--max-misses",
            str(args.max_misses),
            "--timeout-hours",
            str(args.timeout_hours),
        ]
        log_path = os.path.join(PAN_STATE_DIR, slugify(state["title"]) + ".log")
        os.makedirs(PAN_STATE_DIR, exist_ok=True)
        with open(log_path, "ab") as output:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        state["watcher_pid"] = process.pid
        state["watcher_log"] = log_path
        state["status"] = "retrieving"
        save_state(path, state)
        print(
            "PAN_WATCHER_STARTED "
            + json.dumps(
                {"pid": process.pid, "log": log_path, "state": path},
                ensure_ascii=False,
            )
        )
        return 0
    return wait_and_finalize(
        state, path, args.interval, args.max_misses, args.timeout_hours
    )


def command_status(args):
    path = args.state or state_path(args.title)
    state = load_state(path)
    if not state:
        print("PAN_STATE_MISSING")
        return 2
    summary = {
        "title": state.get("title"),
        "status": state.get("status"),
        "share_url": state.get("share_url"),
        "score": state.get("score"),
        "staging_dir": state.get("staging_dir"),
        "final_dir": state.get("final_dir"),
        "transfer_verified_at": state.get("transfer_verified_at"),
        "retrieve_tasks": len(state.get("retrieve_tasks") or []),
        "watcher_pid": state.get("watcher_pid"),
        "watcher_running": pid_is_running(state.get("watcher_pid")),
        "history": state.get("history") or [],
    }
    print("PAN_STATUS " + json.dumps(summary, ensure_ascii=False))
    return 0


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=["plan", "verify-transfer", "verify-retrieve", "set-save-path", "watch", "status"],
    )
    parser.add_argument("--title", default="")
    parser.add_argument("--state", default="")
    parser.add_argument("--candidate-file", default="")
    parser.add_argument("--share-url", default="")
    parser.add_argument("--pass-code", default="")
    parser.add_argument("--match", default="")
    parser.add_argument("--cloud-folder", default=DEFAULT_CLOUD_FOLDER)
    parser.add_argument("--final-dir", default="")
    parser.add_argument("--staging-root", default=STAGING_ROOT)
    parser.add_argument("--media-kind", default="tv")
    parser.add_argument("--series-name", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--season", type=int, default=1)
    parser.add_argument("--cloud-db", default="")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--max-misses", type=int, default=6)
    parser.add_argument("--timeout-hours", type=float, default=12.0)
    parser.add_argument("--detach", action="store_true")
    args = parser.parse_args()
    if not args.title and args.state:
        args.title = load_state(args.state).get("title", "")
    if not args.title:
        parser.error("--title 或 --state 必填")
    if not args.cloud_db:
        from pan_pool import CLOUD_DB

        args.cloud_db = CLOUD_DB
    return args


def main():
    args = parse_args()
    handlers = {
        "plan": command_plan,
        "verify-transfer": command_verify_transfer,
        "verify-retrieve": command_verify_retrieve,
        "set-save-path": command_set_save_path,
        "watch": command_watch,
        "status": command_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
