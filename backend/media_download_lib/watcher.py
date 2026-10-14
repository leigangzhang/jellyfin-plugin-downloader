#!/usr/bin/env python3
"""watcher 状态与进程存活：state/watches 下的状态文件读写。

从原 `backend/media_download_lib.py` 整段搬出，函数体逐字保留，只有跨模块引用
（`STATE_DIR`）改成包内 import。
"""

import json
import os

from .paths import STATE_DIR

__all__ = [
    "pid_is_running",
    "watcher_paths",
    "read_watcher_state",
    "write_watcher_state",
]


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
