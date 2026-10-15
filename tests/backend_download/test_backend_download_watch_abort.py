"""watcher 放弃慢任务时**不许改写迅雷数据库**（根因 #4 的第二条守卫）。

Subject: `backend/download/watch_download.py` 的 `abort_slow_task`。

旧实现是 `pause_task_row(status["taskid"])` —— 直接 `UPDATE etm_task SET state = 2`。
它不改文件（`abort_slow_task` 返回的 cleanup 字典本来就是空的），但仍然绕过客户端改库，
正是根因 #4 里「迅雷的记录与磁盘状态不一致」的同一类动作。迅雷没有暂停/删除类 URL scheme
（`plutil -p /Applications/Thunder.app/Contents/Info.plist` 只注册 thunder / ed2k / magnet /
birdcmd / xunleiapp / thunderExtension，全是「加任务」类），所以正确做法是**不写库**，
只把判定与「任务仍在下」这件事记进日志与状态。

守卫用真库：临时 sqlite 当 `etm_map.db`，跑完比对**字节指纹**（`UPDATE` 一行也逃不过）
与 `state` 列，并断言日志里说明了「任务没被暂停」。
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.backend_download._backend_download_helpers import (
    INFO_HASH,
    VIDEO_NAME,
    etm_db,
    make_temp_etm_db,
    reject_fixture,
    sqlite_fingerprint,
    task_row,
    watch_download,
)

REASON = "sustained ETA above 4.00h"


class SlowTaskAbortTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jmd-watch-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.download_dir, self.video, _ = reject_fixture(self.root)
        self.db_path = make_temp_etm_db(
            os.path.join(self.root, "etm_map.db"),
            [task_row(download_path=self.download_dir)],
        )
        patcher = mock.patch.object(etm_db, "ETM_DB", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def abort(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = watch_download.abort_slow_task(
                INFO_HASH, self.download_dir, {"taskid": 297, "pct": 12.5}, REASON)
        return stream.getvalue(), result

    def stored_state(self):
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT state FROM etm_task WHERE taskid = 297").fetchone()
        finally:
            connection.close()

    def test_the_task_row_is_not_rewritten(self):
        before = sqlite_fingerprint(self.db_path)
        self.abort()
        self.assertEqual(
            sqlite_fingerprint(self.db_path), before,
            "watcher 改写了迅雷的库（旧实现直接 UPDATE state = 2）")

    def test_the_row_still_says_the_task_is_running(self):
        self.abort()
        self.assertEqual(self.stored_state(), (1,))

    def test_the_downloaded_file_is_not_removed(self):
        self.abort()
        self.assertTrue(os.path.isfile(self.video))

    def test_the_return_value_claims_no_removals(self):
        _, result = self.abort()
        self.assertEqual(result["files"], [])
        self.assertEqual(result["bytes"], 0)

    def test_the_log_says_the_task_was_left_running(self):
        """别把功能静默降级：运维必须能从日志看出任务**没有**被暂停。"""
        out, _ = self.abort()
        self.assertIn("ABORT_SLOW_TASK", out)
        self.assertIn(str(297), out)
        self.assertIn(REASON, out)
        self.assertIn("left running", out)


if __name__ == "__main__":
    unittest.main()
