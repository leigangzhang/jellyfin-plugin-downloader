#!/usr/bin/env python3
"""Shared helpers for Thunder download tasks and Jellyfin media finalization."""

import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess

ETM_DB = os.path.expanduser(
    "~/Library/Application Support/Thunder/etm3/etm_map.db"
)
# 媒体库根 / 暂存区：下面这两个值是**兜底默认**（本机历史布局）。正常由插件在
# 启动后端时注入环境变量覆盖：
#   JMD_MEDIA_ROOT   ← 插件从 Jellyfin 媒体库配置里解析出来的库根
#   JMD_STAGING_ROOT ← 插件配置页「暂存目录」（留空则用默认）
# 独立运行（skill / 手跑脚本）时不吃这两个变量，行为与以前一致。
MEDIA_ROOT = os.path.abspath(
    os.environ.get("JMD_MEDIA_ROOT") or "/Volumes/XIAOMI SSD2/Media"
)
STAGING_ROOT = os.path.abspath(
    os.environ.get("JMD_STAGING_ROOT") or "/Volumes/XIAOMI SSD2/.staging"
)
# ---------------------------------------------------------------------------
# 数据根：本插件自带后端**自己维护全部数据**（pool / watches / pan）
#
#   * 默认 = 本文件同级的 state/，随插件目录走，不碰 skill 的
#     `~/Library/Application Support/JellyfinDownloader/`（两边完全隔离）。
#   * `JMD_DATA_DIR` 可覆盖，用于迁移或临时指向别处。
#   * 数据格式以**本插件这份脚本**为准：两边格式若出现冲突，按插件写出的
#     格式走（skill 侧那份只作历史基线，不再作为对齐目标）。
# ---------------------------------------------------------------------------
DATA_ROOT = os.path.abspath(
    os.environ.get("JMD_DATA_DIR")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
)
STATE_DIR = os.path.join(DATA_ROOT, "watches")
POOL_DIR = os.path.join(DATA_ROOT, "pool")
PAN_STATE_DIR = os.path.join(DATA_ROOT, "pan")
THUNDER_PREFS = os.path.expanduser(
    "~/Library/Preferences/com.xunlei.Thunder.plist"
)

VIDEO_EXTS = {".mkv", ".mp4", ".ts", ".avi", ".mov", ".wmv", ".m4v", ".m2ts"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".sup", ".vtt"}
JUNK_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".nfo",
    ".txt",
    ".url",
    ".html",
    ".htm",
    ".doc",
    ".docx",
    ".exe",
    ".js",
}

MIN_VIDEO_BYTES = 100 * 1024 * 1024
GB = 1024**3


def connect_db():
    return sqlite3.connect(ETM_DB)


def find_task_by_hash(info_hash):
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT taskid, state, create_param, bt_task_info
        FROM etm_task
        WHERE upper(json_extract(create_param, '$.info_hash')) = upper(?)
        ORDER BY taskid DESC
        """,
        (info_hash,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def find_task_by_id(taskid):
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT taskid, state, create_param, bt_task_info FROM etm_task WHERE taskid = ?",
        (taskid,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def find_tasks_by_file_name(pattern, sources=("cloud/",)):
    """Find tasks by file name; covers cloud retrieves that have no info hash.

    Cloud "取回本地" tasks carry `source=cloud/getback`, a `url` and an
    `entryid` instead of `info_hash`, so they can only be located by name.
    """
    if not pattern:
        return []
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT taskid, state, create_param, bt_task_info
        FROM etm_task
        WHERE lower(json_extract(create_param, '$.file_name')) LIKE lower(?)
        ORDER BY taskid DESC
        """,
        (f"%{pattern}%",),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    if not sources:
        return rows
    matched = []
    for row in rows:
        param = json.loads(row.get("create_param") or "{}")
        source = str(param.get("source") or "")
        if any(source.startswith(prefix) for prefix in sources):
            matched.append(row)
    return matched or rows


def delete_task_row(taskid):
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM etm_task WHERE taskid = ?", (taskid,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def patch_download_path(taskid, new_path):
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("SELECT create_param FROM etm_task WHERE taskid = ?", (taskid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    param = json.loads(row[0]) if row[0] else {}
    param["download_path"] = ensure_trailing_slash(new_path)
    cur.execute(
        "UPDATE etm_task SET create_param = ? WHERE taskid = ?",
        (json.dumps(param), taskid),
    )
    conn.commit()
    conn.close()
    return True


def patch_task_files(taskid, valid_indices):
    conn = connect_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT create_param, bt_task_info FROM etm_task WHERE taskid = ?",
        (taskid,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return False

    create_param = json.loads(row[0]) if row[0] else {}
    bt_info = json.loads(row[1]) if row[1] else {}
    create_param["select_set"] = valid_indices
    for sub in bt_info.get("subtask", []):
        sub["is_select"] = 1 if int(sub.get("index", -1)) in valid_indices else 0

    cur.execute(
        "UPDATE etm_task SET create_param = ?, bt_task_info = ? WHERE taskid = ?",
        (json.dumps(create_param), json.dumps(bt_info), taskid),
    )
    conn.commit()
    conn.close()
    return True


def pause_task_row(taskid):
    """Mark a task paused without restarting Thunder."""
    conn = connect_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT bt_task_info FROM etm_task WHERE taskid = ?",
        (taskid,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    bt_info = json.loads(row[0]) if row[0] else {}
    for sub in bt_info.get("subtask", []):
        sub["state"] = 2
        sub["download_speed"] = "0"
    cur.execute(
        "UPDATE etm_task SET state = 2, bt_task_info = ? WHERE taskid = ?",
        (json.dumps(bt_info), taskid),
    )
    conn.commit()
    conn.close()
    return True


def task_status(task):
    """Return normalized status for a task row."""
    if not task:
        return None
    bt = json.loads(task.get("bt_task_info") or "{}")
    selected = []
    total = 0
    done = 0
    speed = 0
    for sub in bt.get("subtask", []):
        if not sub.get("is_select", 1):
            continue
        selected.append(sub)
        total += int(sub.get("file_size", 0) or 0)
        done += int(sub.get("download_size", 0) or 0)
        speed += int(sub.get("download_speed", 0) or 0)
    pct = (done / total * 100.0) if total else 0.0
    return {
        "taskid": task["taskid"],
        "state": int(task.get("state", 0) or 0),
        "done": done,
        "total": total,
        "speed": speed,
        "pct": pct,
        "selected": selected,
    }


def cloud_task_status(task):
    """Status for cloud "取回本地" tasks.

    These carry `source=cloud/getback` with `file_name`, `file_size` and
    `download_path` in create_param and no `bt_task_info` subtasks, so the task
    table alone reports 0%. Ground truth is the retrieved file on disk.
    """
    if not task:
        return None
    param = json.loads(task.get("create_param") or "{}")
    file_name = param.get("file_name") or ""
    download_path = param.get("download_path") or ""
    expected = int(param.get("file_size") or 0)
    state = int(task.get("state", 0) or 0)
    found = ""
    if download_path and file_name:
        direct = os.path.join(download_path, file_name)
        if os.path.isfile(direct):
            found = direct
        elif os.path.isdir(download_path):
            for entry in os.listdir(download_path):
                candidate = os.path.join(download_path, entry)
                if not os.path.isfile(candidate):
                    continue
                try:
                    if expected and os.path.getsize(candidate) == expected:
                        found = candidate
                        break
                except OSError:
                    continue
    done = 0
    if found:
        try:
            done = min(allocated_size(found), expected or allocated_size(found))
        except OSError:
            done = 0
    if expected:
        pct = min(done / expected * 100.0, 100.0)
    else:
        pct = 100.0 if found else 0.0
    complete = bool(found) and pct >= 99.5
    return {
        "taskid": task["taskid"],
        "state": state,
        "file_name": file_name,
        "download_path": download_path,
        "expected": expected,
        "done": done,
        "pct": pct,
        "found_path": found,
        "on_disk": bool(found),
        "complete": complete,
        # Thunder says finished but the file is gone: it was already moved out
        # (or deleted) after the retrieve.
        "already_moved": state in (3, 4) and not found,
    }


def active_download_snapshot():
    """Aggregate selected bytes and speed across active Thunder tasks."""
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT taskid, state, create_param, bt_task_info FROM etm_task")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    total = 0
    done = 0
    speed = 0
    tasks = 0
    for row in rows:
        status = task_status(row)
        if not status or status["pct"] > 99.5:
            continue
        if status["state"] not in (0, 1, 2):
            continue
        if status["state"] == 2 and status["speed"] <= 0:
            continue
        total += status["total"]
        done += status["done"]
        speed += status["speed"]
        tasks += 1
    remaining = max(total - done, 0)
    eta_hours = None
    if remaining and speed > 0:
        eta_hours = remaining / speed / 3600
    return {
        "tasks": tasks,
        "total": total,
        "done": done,
        "remaining": remaining,
        "speed": speed,
        "eta_hours": eta_hours,
    }


def task_file_names(task):
    bt = json.loads((task or {}).get("bt_task_info") or "{}")
    names = []
    for sub in bt.get("subtask", []):
        name = sub.get("etm_file_name")
        if name:
            names.append(os.path.basename(name))
    return names


def ensure_trailing_slash(path):
    return path if path.endswith(os.sep) else path + os.sep


def staging_dir_for_final(
    final_dir,
    staging_root=STAGING_ROOT,
    media_root=MEDIA_ROOT,
):
    final_dir = os.path.realpath(final_dir)
    media_root = os.path.realpath(media_root)
    if path_is_within(final_dir, media_root):
        relative = os.path.relpath(final_dir, media_root)
    else:
        relative = os.path.basename(final_dir)
    return os.path.join(os.path.realpath(staging_root), relative)


def normalized_destination(
    source_path,
    final_dir,
    media_kind="auto",
    series_name="",
    year="",
):
    extension = os.path.splitext(source_path)[1].lower()
    basename = os.path.basename(source_path)
    marker_match = re.search(r"S(\d{2})E(\d{2,3})", basename, re.IGNORECASE)
    if media_kind == "tv" and marker_match and series_name:
        season = int(marker_match.group(1))
        episode = int(marker_match.group(2))
        filename = f"{series_name} S{season:02d}E{episode:02d}{extension}"
        return os.path.join(final_dir, f"Season {season:02d}", filename)
    if media_kind == "movie" and series_name:
        suffix = f" ({year})" if year else ""
        return os.path.join(final_dir, f"{series_name}{suffix}{extension}")
    return os.path.join(final_dir, basename)


def atomic_same_volume_move(source, destination):
    source = os.path.realpath(source)
    destination = os.path.abspath(destination)
    if os.path.exists(destination):
        raise FileExistsError(destination)
    if os.stat(os.path.dirname(source)).st_dev != os.stat(
        os.path.dirname(destination)
    ).st_dev:
        raise OSError("source and destination are not on the same volume")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    os.replace(source, destination)
    return destination


def human_gb(num_bytes):
    return num_bytes / GB


def thunder_parallelism():
    """Return Thunder's configured maximum number of simultaneous tasks.

    Thunder stores a zero-based index for a 20-item preset list whose values
    are 1 through 20. Prefer `defaults` so the current in-memory preference is
    used, then fall back to the preference plist.
    """
    index = None
    try:
        result = subprocess.run(
            [
                "defaults",
                "read",
                "com.xunlei.Thunder",
                "maxRunningTasksIndex",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            index = int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        index = None

    if index is None:
        try:
            with open(THUNDER_PREFS, "rb") as handle:
                index = int(plistlib.load(handle).get("maxRunningTasksIndex", 0))
        except (OSError, ValueError, plistlib.InvalidFileException):
            index = 0
    return max(1, min(20, index + 1))


def path_is_within(path, root):
    path = os.path.realpath(path)
    root = os.path.realpath(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def find_release_dirs(download_dir, info_hash, file_names=None):
    """Find only directories created for this torrent/task."""
    file_names = set(file_names or [])
    sidecars = {f".{name}.js" for name in file_names}
    hash_lower = info_hash.lower()
    matches = []
    if not os.path.isdir(download_dir):
        return matches

    for root, dirs, files in os.walk(download_dir):
        score = 0
        for name in files:
            lower = name.lower()
            if name in file_names:
                score += 1
            if name in sidecars:
                score += 4
            if lower.startswith(f".{hash_lower}.js"):
                score += 4
            if lower.startswith(".magent_") and hash_lower in lower:
                score += 4
            if lower.endswith(".torrent") and hash_lower in lower:
                score += 4
        if score and root != os.path.realpath(download_dir):
            matches.append(root)
    return sorted(set(matches), key=len)


def cleanup_task_files(download_dir, info_hash, file_names=None, include_videos=True):
    """Remove only files associated with one Thunder task below download_dir."""
    download_dir = os.path.realpath(download_dir)
    if not os.path.isdir(download_dir):
        return {"files": [], "bytes": 0, "dirs": []}

    file_names = set(file_names or [])
    sidecars = {f".{name}.js" for name in file_names}
    hash_lower = info_hash.lower()
    removed_files = []
    removed_bytes = 0
    touched_dirs = set(find_release_dirs(download_dir, info_hash, file_names))

    for root, _, files in os.walk(download_dir, topdown=False):
        for name in files:
            full = os.path.join(root, name)
            lower = name.lower()
            is_task_artifact = (
                name in file_names
                or name in sidecars
                or (lower.startswith(f".{hash_lower}.js"))
                or (lower.startswith(".magent_") and hash_lower in lower)
                or (lower.endswith(".torrent") and hash_lower in lower)
            )
            if root in touched_dirs and lower.startswith(".magent_") and lower.endswith(".torrent"):
                is_task_artifact = True
            if is_task_artifact:
                try:
                    size = os.path.getsize(full)
                    os.remove(full)
                    removed_files.append(full)
                    removed_bytes += size
                    touched_dirs.add(root)
                except OSError:
                    continue

    for root in sorted(touched_dirs, key=len, reverse=True):
        if root == download_dir or not path_is_within(root, download_dir):
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass

    return {
        "files": removed_files,
        "bytes": removed_bytes,
        "dirs": sorted(touched_dirs),
    }


def query_tasks_for_dir(download_dir, active_only=False):
    target = os.path.realpath(download_dir)
    staging_target = os.path.realpath(staging_dir_for_final(target))
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT taskid, state, create_param, bt_task_info FROM etm_task")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    matches = []
    for row in rows:
        param = json.loads(row.get("create_param") or "{}")
        task_dir = os.path.realpath(param.get("download_path", ""))
        if task_dir not in {target, staging_target}:
            continue
        if active_only and int(row.get("state", 0) or 0) not in (0, 1, 2):
            continue
        matches.append(row)
    return matches


def prune_orphan_residue(download_dir, dry_run=True, protected_dirs=None):
    """Remove inactive Thunder artifacts and empty release directories."""
    target = os.path.realpath(download_dir)
    if not os.path.isdir(target):
        return {"files": [], "dirs": [], "bytes": 0, "protected": []}

    protected = {
        os.path.realpath(os.path.join(target, ".download-candidates")),
    }
    protected.update(
        os.path.realpath(path) for path in (protected_dirs or [])
    )
    for row in query_tasks_for_dir(target, active_only=True):
        param = json.loads(row.get("create_param") or "{}")
        info_hash = (param.get("info_hash") or "").upper()
        names = task_file_names(row)
        protected.update(
            find_release_dirs(target, info_hash, names) if info_hash else []
        )

    def protected_path(path):
        real = os.path.realpath(path)
        return any(path_is_within(real, root) for root in protected)

    removed_files = []
    removed_dirs = []
    removed_bytes = 0

    artifact_names = []
    for root, _, files in os.walk(target, topdown=False):
        if protected_path(root):
            continue
        for name in files:
            lower = name.lower()
            if (
                lower.startswith(".magent_")
                and lower.endswith(".torrent")
            ) or (
                name.startswith(".")
                and lower.endswith(".js")
            ) or lower.endswith(".aria2"):
                artifact_names.append(os.path.join(root, name))

    for path in artifact_names:
        if protected_path(path):
            continue
        try:
            size = os.path.getsize(path)
            if not dry_run:
                os.remove(path)
            removed_files.append(path)
            removed_bytes += size
        except OSError:
            continue

    for root, dirs, _ in os.walk(target, topdown=False):
        if root == target or protected_path(root):
            continue
        if os.path.basename(root) == ".download-candidates":
            continue

        has_complete_video = False
        for current, _, files in os.walk(root):
            for name in files:
                if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                    continue
                path = os.path.join(current, name)
                try:
                    size = os.path.getsize(path)
                    allocated = allocated_size(path)
                except OSError:
                    continue
                if size >= MIN_VIDEO_BYTES and allocated >= size * 0.95:
                    has_complete_video = True
                    break
            if has_complete_video:
                break

        if has_complete_video:
            continue
        try:
            if not dry_run:
                shutil.rmtree(root)
            removed_dirs.append(root)
        except OSError:
            continue

    return {
        "files": removed_files,
        "dirs": removed_dirs,
        "bytes": removed_bytes,
        "protected": sorted(protected),
    }


def probe_torrent_files(info_hash, timeout=25):
    """Fetch torrent metadata and return a list of file dictionaries."""
    import urllib.parse

    trackers = [
        "http://tracker.opentrackr.org:1337/announce",
        "http://tracker.openbittorrent.com:80/announce",
        "http://explodie.org:6969/announce",
    ]
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    for tracker in trackers:
        magnet += f"&tr={urllib.parse.quote(tracker, safe='')}"
    out = f"/tmp/{info_hash}.torrent"
    try:
        os.remove(out)
    except FileNotFoundError:
        pass

    try:
        subprocess.run(
            [
                "aria2c",
                "--bt-metadata-only=true",
                "--bt-save-metadata=true",
                "--seed-time=0",
                f"--bt-stop-timeout={timeout}",
                "--summary-interval=0",
                "--disable-ipv6=true",
                "--bt-tracker-connect-timeout=5",
                "--bt-tracker-timeout=8",
                "--dir=/tmp",
                magnet,
            ],
            capture_output=True,
            timeout=timeout + 10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return []

    if not os.path.exists(out):
        return []

    try:
        result = subprocess.run(
            ["aria2c", "--show-files", out],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return []

    files = []
    lines = result.stdout.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*(\d+)\|(.+)", line)
        if not match:
            continue
        size = 0
        if index + 1 < len(lines):
            size_match = re.search(
                r"([\d.]+)\s*(GiB|MiB|KiB|B)", lines[index + 1]
            )
            if size_match:
                value = float(size_match.group(1))
                unit = size_match.group(2)
                size = int(
                    value
                    * {
                        "GiB": GB,
                        "MiB": 1024**2,
                        "KiB": 1024,
                        "B": 1,
                    }[unit]
                )
        path = match.group(2).strip()
        files.append(
            {
                "index": int(match.group(1)) - 1,
                "path": path,
                "name": os.path.basename(path),
                "size": size,
            }
        )
    return files


def valid_video_files(files, min_bytes=MIN_VIDEO_BYTES):
    videos = []
    for item in files:
        ext = os.path.splitext(item["name"])[1].lower()
        if ext in VIDEO_EXTS and item["size"] >= min_bytes:
            videos.append(item)
    return sorted(videos, key=lambda item: item["path"])


def allocated_size(path):
    stat = os.stat(path)
    return getattr(stat, "st_blocks", 0) * 512


def probe_media(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size:"
                "stream=index,codec_type,codec_name,width,height,channels,tags"
            ),
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffprobe failed").strip())
    return json.loads(result.stdout)


def external_subtitle_files(video_path):
    stem = os.path.splitext(video_path)[0]
    matches = []
    for ext in SUBTITLE_EXTS:
        candidate = stem + ext
        if os.path.isfile(candidate):
            matches.append(candidate)
    return matches


def validate_subtitle_file(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < 100:
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext == ".sup":
        return size >= 1024
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return False
    if ext == ".vtt":
        return bool(re.search(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->", text))
    if ext in {".srt", ".ass", ".ssa"}:
        return bool(re.search(r"\d{1,2}:\d{2}:\d{2}", text))
    return bool(text.strip())


def decode_check(path, timeout=300):
    try:
        process = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-xerror",
                "-i",
                path,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "full decode timed out", ""
    except OSError as exc:
        return False, f"ffmpeg failed to start: {exc}", ""
    stderr = (process.stderr or "").strip()
    if process.returncode != 0:
        return False, stderr[-2000:] or "ffmpeg decode failed", stderr[-2000:]
    return True, "", stderr[-2000:]


def verify_video(
    path,
    min_duration_seconds=60,
    deep=True,
    decode_timeout=300,
):
    """Validate allocation, streams, full decode, and subtitle availability."""
    result = {
        "path": path,
        "ok": False,
        "reason": "",
        "size": 0,
        "allocated": 0,
        "duration": 0.0,
        "video_streams": 0,
        "audio_streams": 0,
        "subtitle_streams": 0,
        "embedded_subtitles": [],
        "external_subtitles": [],
        "subtitle_ok": False,
        "decode_ok": False,
        "decode_warning": "",
    }
    if not os.path.isfile(path):
        result["reason"] = "missing"
        return result

    result["size"] = os.path.getsize(path)
    result["allocated"] = allocated_size(path)
    if result["size"] < MIN_VIDEO_BYTES:
        result["reason"] = "video is smaller than 100MB"
        return result
    if result["allocated"] < result["size"] * 0.95:
        result["reason"] = "file is sparse or incomplete"
        return result

    try:
        payload = probe_media(path)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result["reason"] = f"ffprobe failed: {exc}"
        return result

    try:
        result["duration"] = float(payload.get("format", {}).get("duration", 0) or 0)
        streams = payload.get("streams", [])
        video_streams = [
            stream for stream in streams if stream.get("codec_type") == "video"
        ]
        audio_streams = [
            stream for stream in streams if stream.get("codec_type") == "audio"
        ]
        subtitle_streams = [
            stream for stream in streams if stream.get("codec_type") == "subtitle"
        ]
        result["video_streams"] = len(video_streams)
        result["audio_streams"] = len(audio_streams)
        result["subtitle_streams"] = len(subtitle_streams)
        result["embedded_subtitles"] = [
            {
                "index": stream.get("index"),
                "codec": stream.get("codec_name"),
                "tags": stream.get("tags", {}),
            }
            for stream in subtitle_streams
        ]
    except (TypeError, ValueError):
        result["reason"] = "ffprobe returned invalid JSON"
        return result

    if not result["video_streams"]:
        result["reason"] = "no video stream"
        return result
    if result["duration"] < min_duration_seconds:
        result["reason"] = f"duration is only {result['duration']:.1f}s"
        return result

    external = external_subtitle_files(path)
    result["external_subtitles"] = [
        subtitle for subtitle in external if validate_subtitle_file(subtitle)
    ]
    result["subtitle_ok"] = bool(
        result["embedded_subtitles"] or result["external_subtitles"]
    )

    if deep:
        decode_ok, decode_reason, decode_warning = decode_check(
            path,
            timeout=decode_timeout,
        )
        result["decode_ok"] = decode_ok
        result["decode_warning"] = decode_warning
        if not decode_ok:
            result["reason"] = f"ffmpeg decode failed: {decode_reason}"
            return result

    result["ok"] = True
    return result


def audit_extra_files(download_dir, video_paths=None):
    """Report non-media files that are not recognized Jellyfin metadata."""
    target = os.path.realpath(download_dir)
    video_paths = {os.path.realpath(path) for path in (video_paths or [])}
    metadata_ext = {".nfo", ".jpg", ".jpeg", ".png", ".webp"}
    extra = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [
            name
            for name in dirs
            if name not in {".download-candidates", "@eaDir"}
        ]
        for name in files:
            path = os.path.join(root, name)
            real = os.path.realpath(path)
            ext = os.path.splitext(name)[1].lower()
            if real in video_paths:
                continue
            if ext in VIDEO_EXTS or ext in SUBTITLE_EXTS:
                continue
            if ext in metadata_ext:
                continue
            if name in {".DS_Store", "Thumbs.db"}:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            extra.append({"path": path, "size": size})
    return extra


def cleanup_release_junk(release_dir):
    removed = []
    for root, _, files in os.walk(release_dir):
        for name in files:
            full = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            if name.startswith(".magent_") and name.endswith(".torrent"):
                try:
                    os.remove(full)
                    removed.append(full)
                except OSError:
                    pass
                continue
            if name.startswith(".") and name.endswith(".js"):
                try:
                    os.remove(full)
                    removed.append(full)
                except OSError:
                    pass
                continue
            if ext in VIDEO_EXTS or ext in SUBTITLE_EXTS or name.startswith("."):
                continue
            if ext in JUNK_EXTS or os.path.getsize(full) < 5 * 1024 * 1024:
                try:
                    os.remove(full)
                    removed.append(full)
                except OSError:
                    pass
    return removed


def finalize_download(
    download_dir,
    info_hash,
    min_duration_seconds=60,
    deep_verify=False,
    final_dir=None,
    media_kind="auto",
    series_name="",
    year="",
):
    """Validate, clean, and flatten a completed download."""
    download_dir = os.path.realpath(download_dir)
    final_dir = os.path.realpath(final_dir or download_dir)
    task = None
    rows = find_task_by_hash(info_hash)
    if rows:
        task = rows[0]
    file_names = task_file_names(task) if task else []
    release_dirs = find_release_dirs(download_dir, info_hash, file_names)
    search_dirs = release_dirs or [download_dir]

    verified = []
    invalid = []
    removed_junk = []
    moved = []

    for release_dir in search_dirs:
        removed_junk.extend(cleanup_release_junk(release_dir))
        for entry in os.listdir(release_dir):
            source = os.path.join(release_dir, entry)
            if not os.path.isfile(source):
                continue
            ext = os.path.splitext(entry)[1].lower()
            if ext in VIDEO_EXTS:
                check = verify_video(
                    source,
                    min_duration_seconds,
                    deep=deep_verify,
                )
                if check["ok"]:
                    verified.append(check)
                    destination = normalized_destination(
                        source,
                        final_dir,
                        media_kind=media_kind,
                        series_name=series_name,
                        year=year,
                    )
                    if os.path.realpath(source) != os.path.realpath(destination):
                        moved_path = atomic_same_volume_move(
                            source,
                            destination,
                        )
                        moved.append(moved_path)
                else:
                    invalid.append(check)
            elif ext in SUBTITLE_EXTS:
                destination = normalized_destination(
                    source,
                    final_dir,
                    media_kind=media_kind,
                    series_name=series_name,
                    year=year,
                )
                if (
                    os.path.realpath(source) != os.path.realpath(destination)
                    and not os.path.exists(destination)
                ):
                    moved.append(atomic_same_volume_move(source, destination))

    for release_dir in sorted(release_dirs, key=len, reverse=True):
        if not path_is_within(release_dir, download_dir):
            continue
        try:
            os.rmdir(release_dir)
        except OSError:
            pass

    extra_files = audit_extra_files(final_dir)
    warnings = []
    for item in verified:
        if not item.get("subtitle_ok"):
            warnings.append(f"no valid subtitle found for {item['path']}")
        if item.get("decode_warning"):
            warnings.append(
                f"decode warning for {item['path']}: {item['decode_warning']}"
            )
    if extra_files:
        warnings.append(f"{len(extra_files)} extra file(s) found")

    return {
        "verified": verified,
        "invalid": invalid,
        "removed_junk": removed_junk,
        "moved": moved,
        "release_dirs": release_dirs,
        "extra_files": extra_files,
        "warnings": warnings,
    }


def pid_is_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def watcher_paths(info_hash):
    os.makedirs(STATE_DIR, exist_ok=True)
    base = os.path.join(STATE_DIR, info_hash.upper())
    return f"{base}.json", f"{base}.log"


def read_watcher_state(info_hash):
    state_path, _ = watcher_paths(info_hash)
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_watcher_state(info_hash, payload):
    state_path, _ = watcher_paths(info_hash)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path)
