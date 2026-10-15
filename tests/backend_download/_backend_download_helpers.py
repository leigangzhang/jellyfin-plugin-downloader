"""共享件：`backend/` 上路径，以及提交/监视线路共用的假件与临时迅雷任务库。

这里没有用例（`_` 前缀不会被 `unittest discover` 收集）。四样东西：

* `backend/` 进 `sys.path` —— 被测对象是插件后端，不在 `tests/harness.py` 那个根里；
* `HandlerStub` —— `_api_search` / `_api_submit` 只用到 `self._ok` / `self._bad`，
  用真实的 `ConsoleHandler` 要一路造 socket，所以给一个只记录调用的替身；
* `make_temp_etm_db` / `sqlite_fingerprint` —— 根因 #4 的两条守卫都要求「任务行没被改写」，
  而「没被改写」只有对着**真库**才测得出来：拿临时 sqlite 当 `etm_map.db`（并 patch
  `media_download_lib.db.ETM_DB`），跑完比对字节指纹 —— `pause_task_row` 只改一行也能测出来；
* `reject_fixture` —— 「已经下起来的」下载目录（发布目录 + 视频 + t35 的 metainfo）。

边界（`valid_target_dir`、`dispatch_search`、`service.run_cli`）在各用例里用
`mock.patch.object` 单独换掉，这里不预置，免得读代码的人看不出哪条边界被替换了。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import build_console as bc  # noqa: E402,F401 -- re-exported: 这一行保证 BACKEND 已进 sys.path
import console_server.http_api as http_api  # noqa: E402
import download.submit_xunlei as submit_xunlei  # noqa: E402
import download.watch_download as watch_download  # noqa: E402
import media_download_lib.db as etm_db  # noqa: E402
import source_config  # noqa: E402

INFO_HASH = "F0917E25C9220BDD79BD581A897C2CED4B22F95C"
VIDEO_NAME = "One.Battle.After.Another.2025.1080p.WMAX.WEB-DL.mkv"
RELEASE_DIR = "【高清影视之家发布 www.HDBTHD.com】一战再战[中文字幕]"
MOVIE_DIR = "一战再战 (2025)"

ETM_TASK_SCHEMA = """
CREATE TABLE etm_task (
    taskid INTEGER PRIMARY KEY,
    state INTEGER,
    create_param TEXT,
    bt_task_info TEXT
)
"""


class HandlerStub:
    """替身 Handler：只实现 `_ok` / `_bad`，够 `_api_search` / `_api_submit` 直接跑。"""

    def __init__(self):
        self.sent = []

    def _ok(self, obj):
        self.sent.append(("ok", obj))

    def _bad(self, msg):
        self.sent.append(("bad", msg))

    @property
    def last(self):
        return self.sent[-1] if self.sent else (None, None)


def fake_run_cli(cmds):
    """给 `service.run_cli` 用的替身：记下命令行，返回成功。"""

    def run(cmd, timeout):
        cmds.append(list(cmd))
        return 0, "fake stdout", ""

    return run


def task_row(taskid=297, info_hash=INFO_HASH, download_path="", video_name=VIDEO_NAME):
    """一条最小可用的迅雷任务行（列名与真库一致）。"""
    bt_info = {
        "subtask": [
            {
                "index": 0,
                "etm_file_name": video_name,
                "file_size": 4096,
                "is_select": 1,
                "state": 1,
                "download_speed": "1234",
            }
        ]
    }
    create_param = {
        "info_hash": info_hash,
        "download_path": download_path,
        "file_name": MOVIE_DIR,
    }
    return {
        "taskid": taskid,
        "state": 1,
        "create_param": json.dumps(create_param, ensure_ascii=False),
        "bt_task_info": json.dumps(bt_info, ensure_ascii=False),
    }


def make_temp_etm_db(path, rows, schema=ETM_TASK_SCHEMA):
    """造一个临时迅雷任务库并把 rows 写进去；返回库路径。"""
    connection = sqlite3.connect(path)
    try:
        connection.execute(schema)
        for row in rows:
            connection.execute(
                "INSERT INTO etm_task (taskid, state, create_param, bt_task_info)"
                " VALUES (?, ?, ?, ?)",
                (row["taskid"], row.get("state", 1),
                 row.get("create_param", "{}"), row.get("bt_task_info", "{}")),
            )
        connection.commit()
    finally:
        connection.close()
    return path


def sqlite_fingerprint(path):
    """库的字节级指纹；连 WAL 一起算 —— `pause_task_row` 只改一行也会改变它。"""
    digest = hashlib.sha256()
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            digest.update(suffix.encode("utf-8"))
            digest.update(candidate.read_bytes())
    return digest.hexdigest()


def reject_fixture(root):
    """造一份「已经下起来的」下载目录：发布目录里一个视频 + 任务目录里一份 metainfo。"""
    base = os.path.join(root, "Movies", MOVIE_DIR)
    release = os.path.join(base, RELEASE_DIR)
    os.makedirs(release, exist_ok=True)
    video = os.path.join(release, VIDEO_NAME)
    with open(video, "wb") as handle:
        handle.write(b"V" * 4096)
    # t35 写的那份 metainfo：文件名带 info_hash，暂停后恢复要靠它
    magent = os.path.join(base, f".magent_{INFO_HASH}.torrent")
    with open(magent, "wb") as handle:
        handle.write(b"d4:infod4:name4:teste")
    return base, video, magent
