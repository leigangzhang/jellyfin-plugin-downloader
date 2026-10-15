"""拒绝路径必须**保留数据**：不删文件、不删任务行、不直接写迅雷 SQLite。

Subject: `backend/download/submit_xunlei.py` 的 `reject_and_preserve` /
`reject_patch_failure` / `quarantine_task_files`。

实测（根因 #4，2026-10-15，见 /tmp/jmd-test/evidence/ROOTCAUSE-4-reject-deletes-data.md）：
安装版 `reject_and_cleanup()` 先 `pause_task_row` + `delete_task_row`（**直接改迅雷 SQLite**），
再 `cleanup_task_files(..., include_videos=True)` 把刚下好的文件删掉 —— 迅雷那边任务还在、
数据没了，用户「暂停后再继续」就报「获取种子文件失败 / 请检查下载过程中的文件是否丢失」。
`rc=5 PATCH_NOT_APPLIED`（297/299）与 `ETA_REJECTED`（303）两条路径都踩了它；唯一没走拒绝
路径的 301 数据完整保留（反证）。

这一组**不 mock 文件操作也不 mock 数据库**：临时 sqlite 当 `etm_map.db`、真目录里放真文件，
跑完同时比对「视频还在不在」与「库字节指纹变没变」。两条断言在改造前都是红的（旧实现既删
文件又 UPDATE 任务行）。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.backend_download._backend_download_helpers import (
    INFO_HASH,
    MOVIE_DIR,
    RELEASE_DIR,
    VIDEO_NAME,
    etm_db,
    make_temp_etm_db,
    reject_fixture,
    sqlite_fingerprint,
    submit_xunlei,
    task_row,
)


class RejectTestCase(unittest.TestCase):
    """共用夹具：真目录 + 临时迅雷库；库路径与文件操作的边界都不 mock。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jmd-reject-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.download_dir, self.video, self.magent = reject_fixture(self.root)
        self.db_path = make_temp_etm_db(
            os.path.join(self.root, "etm_map.db"),
            [task_row(download_path=self.download_dir)],
        )
        self.patch(etm_db, "ETM_DB", self.db_path)

    def patch(self, target, name, new=mock.DEFAULT):
        patcher = (mock.patch.object(target, name, new) if new is not mock.DEFAULT
                   else mock.patch.object(target, name))
        self.addCleanup(patcher.stop)
        return patcher.start()

    def fingerprint(self):
        return sqlite_fingerprint(self.db_path)

    def stored_state(self, taskid=297):
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT state FROM etm_task WHERE taskid = ?", (taskid,)).fetchone()
        finally:
            connection.close()


class RejectPreservesDataTests(RejectTestCase):
    """`rc=5` 的收口（`reject_patch_failure`）就是根因 #4 的现场：它必须什么都不动。"""

    def run_patch_failure(self, taskid=297):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            rc = submit_xunlei.reject_patch_failure(
                taskid, self.download_dir, "select_set mismatch: [0, 1, 2, 3]")
        out = stream.getvalue()
        line = [l for l in out.splitlines() if l.startswith("PATCH_NOT_APPLIED")][0]
        return rc, out, json.loads(line.split(" ", 1)[1])

    def test_still_returns_five_and_keeps_the_output_label(self):
        rc, out, _ = self.run_patch_failure()
        self.assertEqual(rc, 5)
        self.assertIn("PATCH_NOT_APPLIED", out)
        self.assertIn("select_set mismatch: [0, 1, 2, 3]", out)

    def test_the_downloaded_video_is_still_on_disk(self):
        self.run_patch_failure()
        self.assertTrue(
            os.path.isfile(self.video),
            "拒绝后视频不许消失（根因 #4 就是它被 cleanup_task_files 删了）")

    def test_the_metainfo_is_still_on_disk(self):
        """t35 的语义：恢复下载要靠这份 metainfo，删了就等于再也回不来。"""
        self.run_patch_failure()
        self.assertTrue(os.path.isfile(self.magent))

    def test_the_release_directory_is_not_removed(self):
        self.run_patch_failure()
        self.assertTrue(os.path.isdir(os.path.join(self.download_dir, RELEASE_DIR)))

    def test_the_task_row_is_not_rewritten(self):
        before = self.fingerprint()
        self.run_patch_failure()
        self.assertEqual(
            self.fingerprint(), before,
            "拒绝路径改写了迅雷的库（旧实现 pause_task_row + delete_task_row 都动它）")
        self.assertEqual(self.stored_state(), (1,), "任务行必须原样留在库里")

    def test_the_json_says_the_task_and_the_data_are_kept(self):
        _, _, payload = self.run_patch_failure()
        self.assertIs(payload["task_kept"], True)
        self.assertIs(payload["data_preserved"], True)
        self.assertIs(payload["db_untouched"], True)
        self.assertEqual(payload["removed_files"], 0)
        self.assertTrue(payload["action_required"])

    def test_missing_row_is_reported_without_pretending_to_keep_it(self):
        _, _, payload = self.run_patch_failure(taskid=999)
        self.assertIs(payload["task_kept"], False)


class RejectAndPreserveTests(RejectTestCase):
    """默认语义：`reject_and_preserve` 只记录，不动文件也不动库。"""

    def reject(self, **kwargs):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            payload = submit_xunlei.reject_and_preserve(
                task_row(), self.download_dir, "sustained ETA above 4.00h", **kwargs)
        return stream.getvalue(), payload

    def test_the_output_keeps_the_old_label_and_the_reason(self):
        out, _ = self.reject()
        self.assertIn("ETA_REJECTED", out, "既有输出标签不能变")
        self.assertIn("Rejecting task:", out)

    def test_nothing_is_deleted_or_moved(self):
        self.reject()
        self.assertTrue(os.path.isfile(self.video))
        self.assertTrue(os.path.isfile(self.magent))
        self.assertTrue(os.path.isdir(os.path.join(self.download_dir, RELEASE_DIR)))

    def test_no_direct_write_to_thunder_sqlite(self):
        """acceptance 3：拒绝路径不许直接改迅雷的库（暂停/删除都不行）。"""
        before = self.fingerprint()
        self.reject()
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.stored_state(), (1,))

    def test_the_output_says_the_task_and_the_data_are_kept(self):
        _, payload = self.reject()
        self.assertIs(payload["task_kept"], True)
        self.assertIs(payload["data_preserved"], True)
        self.assertIs(payload["db_untouched"], True)
        self.assertEqual(payload["removed_files"], 0)
        self.assertTrue(payload["action_required"])

    def test_restart_thunder_is_no_longer_needed_but_does_not_block(self):
        """保留语义下不再重启迅雷（重启是给「改完库」收尾的），且不阻断输出。"""
        out, _ = self.reject(restart_thunder=True)
        self.assertIn("ETA_REJECTED", out)


class QuarantineTests(RejectTestCase):
    """确需清理时只能**移动**到可回滚位置，并留下「谁/何时/为什么/搬去哪」。"""

    def setUp(self):
        super().setUp()
        self.staging = os.path.join(self.root, "staging")
        shutil.move(os.path.join(self.root, "Movies"),
                    os.path.join(self.staging, "Media", "Movies"))
        self.download_dir = os.path.join(self.staging, "Media", "Movies", MOVIE_DIR)
        self.video = os.path.join(self.download_dir, RELEASE_DIR, VIDEO_NAME)
        self.patch(submit_xunlei, "STAGING_ROOT", self.staging)
        self.rejected_root = os.path.join(self.staging, ".rejected")

    def quarantine(self, **kwargs):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            payload = submit_xunlei.reject_and_preserve(
                task_row(), self.download_dir, "manual cleanup requested",
                quarantine=True, rejected_by="test-suite", **kwargs)
        return stream.getvalue(), payload

    def test_files_are_moved_not_deleted(self):
        _, payload = self.quarantine()
        self.assertFalse(os.path.isfile(self.video), "隔离后原位置应当空了")
        moved = [os.path.join(root, name)
                 for root, _, names in os.walk(self.rejected_root) for name in names]
        self.assertTrue(moved, "搬走的东西必须能在 .rejected/ 下找到")
        self.assertEqual(payload["quarantined_to"],
                         os.path.join(self.rejected_root, INFO_HASH.upper()))
        self.assertEqual(payload["removed_files"], 0, "移动不是删除")

    def test_the_record_says_who_when_why_and_where(self):
        self.quarantine()
        record_path = os.path.join(self.rejected_root, INFO_HASH.upper(), "REJECTED.json")
        with open(record_path, encoding="utf-8") as handle:
            record = json.load(handle)
        for key in ("info_hash", "taskid", "reason", "rejected_by", "rejected_at",
                    "moved_from", "moved_to", "files"):
            self.assertIn(key, record, f"记录必须写清 {key}")
        self.assertEqual(record["rejected_by"], "test-suite")
        self.assertEqual(record["reason"], "manual cleanup requested")
        self.assertEqual(record["info_hash"], INFO_HASH.upper())

    def test_quarantine_does_not_touch_the_task_row(self):
        before = self.fingerprint()
        self.quarantine()
        self.assertEqual(self.fingerprint(), before)

    def test_quarantine_keeps_the_task_directory_itself(self):
        """只搬任务产物，不动 <片名 (年份)> 这一层目录（它可能还被别的任务共用）。"""
        self.quarantine()
        self.assertTrue(os.path.isdir(self.download_dir))


if __name__ == "__main__":
    unittest.main()
