#!/usr/bin/env python3
"""迅雷任务库（etm_map.db）读取与写入：任务行查询、状态归一化、路径补丁。

从原 `backend/media_download_lib.py` 整段搬出，函数体逐字保留，只有跨模块引用
（`allocated_size`、`ensure_trailing_slash`）改成包内 import。

`cloud_task_status` 原来嵌套 7 层，这里只把「在盘上找回取回文件」和「把判定结果
拼成返回结构」两段抽成模块级私有函数，判定顺序、分支条件与返回键一字未动。
"""

import json
import os
import sqlite3

from ._shared import allocated_size, ensure_trailing_slash
from .paths import ETM_DB

__all__ = [
    "connect_db",
    "find_task_by_hash",
    "find_task_by_id",
    "find_tasks_by_file_name",
    "delete_task_row",
    "patch_download_path",
    "patch_task_files",
    "pause_task_row",
    "task_status",
    "cloud_task_status",
    "active_download_snapshot",
    "task_file_names",
]


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


def _find_retrieved_file(download_path, file_name, expected):
    """取回本地任务的落盘文件：先按名字直取，再按预期字节数扫描目录。

    返回找到的绝对路径；没找到返回空串。等价于原来的内联分支，只是把两层
    `if os.path.isdir` / `for` / `try` 的嵌套拍平成一个函数。
    """
    if not (download_path and file_name):
        return ""
    direct = os.path.join(download_path, file_name)
    if os.path.isfile(direct):
        return direct
    if not os.path.isdir(download_path):
        return ""
    for entry in os.listdir(download_path):
        candidate = os.path.join(download_path, entry)
        if not os.path.isfile(candidate):
            continue
        try:
            if expected and os.path.getsize(candidate) == expected:
                return candidate
        except OSError:
            continue
    return ""


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
    found = _find_retrieved_file(download_path, file_name, expected)
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
