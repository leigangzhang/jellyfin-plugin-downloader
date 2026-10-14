"""清理守卫：仍在迅雷 DB 里的任务，脚下的目录不是残留。

现场（2026-10-13）：三个 `state=4`（失败/暂停）的任务被 `prune_orphan_residue` 当成
残留，`download_path/file_name/` 目录连同 `.magent_<UUID>.torrent` 被 `os.remove` 永久
删除（不进废纸篓），之后恢复下载报「获取种子文件失败，无法继续下载」，数据无法找回。
根因是保护范围用了 `active_only=True`，把 state 不在 (0,1,2) 的任务排除在外。

这些用例全部离线：DB 读取被打桩，不连迅雷、不碰真实下载盘。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import cleanup_download as cd  # noqa: E402
import media_download_lib as mdl  # noqa: E402
import search_pool as sp  # noqa: E402

TASK_NAME = "Wednesday (2022) Season 1 S01 1080p"
MAGENT = ".magent_7ED80D7D-5ABE-4E0A-94FF-2450B106EBE3.torrent"


def make_tree(root: Path) -> Path:
    """构造一个与现场同形的任务目录：`<root>/<task name>/<.magent>.torrent`。"""
    task_dir = root / TASK_NAME
    task_dir.mkdir(parents=True)
    (task_dir / MAGENT).write_bytes(b"d4:infod4:name4:testee")
    (task_dir / ".aria2").write_text("control", encoding="utf-8")
    return task_dir


def task_row(state: int) -> dict:
    return {
        "taskid": 285,
        "state": state,
        "create_param": (
            '{"download_path": "%(path)s", "file_name": "%(name)s", '
            '"info_hash": "9C49DAE75AF85932215B640E53F58C0B757DBE02"}'
        )
        % {"path": "", "name": TASK_NAME},
        "bt_task_info": "{}",
    }


class ReferencedTaskIsNeverResidue(unittest.TestCase):
    """任何 state 的任务都受保护——state=4 是「暂停/失败」，不是「不要了」。"""

    def _run(self, state: int, referenced: bool) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="jmd-cleanup-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        task_dir = make_tree(tmp)
        row = task_row(state)
        param = __import__("json").dumps(
            {"download_path": str(tmp), "file_name": TASK_NAME,
             "info_hash": "9C49DAE75AF85932215B640E53F58C0B757DBE02"}
        )
        row["create_param"] = param
        rows = [row] if referenced else []

        # 桩必须复刻真函数的 `active_only` 过滤语义，否则它无法钉住守卫：
        # 旧实现传 active_only=True 时，state=4 的任务会被排除在保护之外，
        # 这个桩要如实反映那件事，红证明才会真的变红。
        def fake_query_tasks_for_dir(_dir, active_only=False):
            if active_only and int(row.get("state", 0) or 0) not in (0, 1, 2):
                return []
            return rows

        with mock.patch.object(mdl, "query_tasks_for_dir", side_effect=fake_query_tasks_for_dir), \
             mock.patch.object(mdl, "find_release_dirs", return_value=[]):
            mdl.prune_orphan_residue(str(tmp), dry_run=False)
        return task_dir

    def test_state_four_task_keeps_its_torrent_and_directory(self):
        task_dir = self._run(state=4, referenced=True)
        self.assertTrue(task_dir.is_dir(), "任务目录被删了")
        self.assertTrue((task_dir / MAGENT).exists(), "任务的 .magent 种子被删了")

    def test_state_one_task_is_protected_too(self):
        task_dir = self._run(state=1, referenced=True)
        self.assertTrue((task_dir / MAGENT).exists())

    def test_unreferenced_artifacts_are_still_cleaned(self):
        """负证明：没有任何任务引用的残留仍应被清掉，否则守卫就变成了什么都不做。"""
        task_dir = self._run(state=4, referenced=False)
        self.assertFalse((task_dir / MAGENT).exists(), "无人引用的 .magent 应被删除")


class CleanupCliRefusesReferencedTasks(unittest.TestCase):
    """CLI 默认拒绝删除仍在 DB 里的任务，必须显式 --allow-task-deletion。

    离线用例：`subprocess.run` 整体打桩，`--no-thunder-restart` 再兜一层。
    2026-10-14 之前这里没打 `subprocess` 桩，`force=True` 那条用例会真的执行
    `cleanup_download.py` 的关/开迅雷动作——每跑一次测试就把用户的迅雷杀一次再拉起来。
    """

    def _run_cli(self, force: bool, no_restart: bool = True) -> tuple[int, str, mock.Mock]:
        import contextlib
        import io

        row = task_row(4)
        row["bt_task_info"] = (
            '{"subtask": [{"is_select": 1, "file_size": 100, "download_size": 15}]}'
        )
        argv = ["cleanup_download.py", "9C49DAE7", "/tmp/whatever"]
        if force:
            argv.append("--allow-task-deletion")
        if no_restart:
            argv.append("--no-thunder-restart")
        buf = io.StringIO()
        with mock.patch.object(cd.subprocess, "run") as run:
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(cd, "find_task_by_hash", return_value=[row]), \
                 mock.patch.object(cd, "task_file_names", return_value=[TASK_NAME]), \
                 mock.patch.object(cd, "cleanup_task_files", return_value={"files": [], "bytes": 0, "dirs": []}), \
                 mock.patch.object(cd, "delete_task_row", return_value=True), \
                 contextlib.redirect_stdout(buf):
                code = cd.main()
        return code, buf.getvalue(), run

    def test_default_refuses_a_low_progress_state_four_task(self):
        code, out, run = self._run_cli(force=False)
        self.assertEqual(code, 3)
        self.assertIn("REFUSING_TO_DELETE_REFERENCED_TASK", out)
        run.assert_not_called()

    def test_explicit_flag_is_the_only_way_through(self):
        code, out, run = self._run_cli(force=True)
        self.assertNotIn("REFUSING_TO_DELETE_REFERENCED_TASK", out)
        self.assertIn("CLEANUP_DONE", out)
        # 离线用例一个 OS 动作都不许发——尤其不许碰宿主的迅雷。
        run.assert_not_called()

    def test_restart_can_be_opted_out_but_stays_on_by_default(self):
        """`--no-thunder-restart` 只关掉关/开动作，默认（不传）仍会执行它们。"""
        code, out, run = self._run_cli(force=True, no_restart=False)
        self.assertEqual(code, 0)
        self.assertIn("CLEANUP_DONE", out)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["osascript", "-e", 'quit app "Thunder"'],
                ["pkill", "-9", "-f", "Thunder"],
                ["open", "-a", "Thunder"],
            ],
        )


if __name__ == "__main__":
    unittest.main()


class WriteTaskMagentIsDeterministic(unittest.TestCase):
    """`.magent` 由我们写入，不等迅雷——迅雷有时不落盘，之后恢复就报「获取种子文件失败」。

    实测（2026-10-13）：任务 287/289 的任务目录是空的，`.magent` 全盘 0 命中，
    手工按记录路径写回后迅雷即可恢复。所以写入必须是插件的确定性动作。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jmd-magent-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.source = self.tmp / "src.torrent"
        self.source.write_bytes(b"d4:infod4:name4:testee")
        self.task_dir = self.tmp / "task"

    def test_creates_the_task_dir_and_writes_a_readable_file(self):
        result = mdl.write_task_magent(str(self.task_dir), "AB" * 20, source=str(self.source))
        self.assertTrue(result["written"])
        self.assertTrue(os.path.isfile(result["path"]))
        self.assertGreater(result["bytes"], 0)
        self.assertEqual(len(mdl.task_magent_paths(str(self.task_dir))), 1)

    def test_is_idempotent_when_a_usable_torrent_already_exists(self):
        first = mdl.write_task_magent(str(self.task_dir), "AB" * 20, source=str(self.source))
        second = mdl.write_task_magent(str(self.task_dir), "AB" * 20, source=str(self.source))
        self.assertFalse(second["written"])
        self.assertTrue(second["existed"])
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(len(mdl.task_magent_paths(str(self.task_dir))), 1)

    def test_reports_an_error_when_no_metainfo_is_available(self):
        result = mdl.write_task_magent(
            str(self.tmp / "other"), "CD" * 20, source=str(self.tmp / "missing.torrent"))
        self.assertEqual(result["path"], "")
        self.assertFalse(result["written"])
        self.assertIn("没有可用的 metainfo", result["error"])

    def test_empty_existing_file_does_not_count_as_usable(self):
        self.task_dir.mkdir(parents=True)
        empty = self.task_dir / ".magent_EMPTY.torrent"
        empty.write_bytes(b"")
        result = mdl.write_task_magent(str(self.task_dir), "AB" * 20, source=str(self.source))
        self.assertTrue(result["written"])
        self.assertGreater(result["bytes"], 0)


class PublisherOnlySelection(unittest.TestCase):
    """单源判据：只看名字（索引不返回发布者字段），与打分侧偏好共用同一组 token。"""

    TITLE = "【高清剧集网发布 www.DDHDTV.com】星期三.第二季[简繁英字幕].Wednesday.S02.2160p"

    def test_matches_both_publisher_tokens(self):
        self.assertTrue(sp.is_publisher_candidate(self.TITLE))
        self.assertTrue(sp.is_publisher_candidate("某剧 DDHDTV 1080p"))
        self.assertTrue(sp.is_publisher_candidate("高清剧集网发布 某剧"))

    def test_rejects_other_publishers(self):
        self.assertFalse(sp.is_publisher_candidate("Wednesday (2022) S01 1080p BluRay x265"))
        self.assertFalse(sp.is_publisher_candidate(""))
        self.assertFalse(sp.is_publisher_candidate(None))
