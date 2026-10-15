"""提交→监控→检测归档 这条状态机的后端契约。

Subject: `backend/console_server/http_api.py` 的 `_api_submit` / `_get_stages`，以及
`backend/download/watch_download.py` 的阶段写入与存活判定。

四组用例对着验收的四类：

* **提交不等 ETA**：`wait_eta: false` ⇒ 命令行带 `--eta-probe-seconds 0`（且不带
  `--no-watch`，由提交侧直接接上监控），返回体给出 `taskid`/`info_hash`/`final_dir`
  与 `eta_deferred=true`；**不传 `wait_eta` 时命令行与响应与改动前逐字一致**。
* **阶段状态写入与读取**：`GET /api/stages` 一次给出 `{info_hash: {stage, …}}`，
  新文件读 `stage`、旧文件按 `status` 映射。
* **任务消失 → failed_download**（原因「任务已从迅雷消失」）。
* **暂停 → paused**（迅雷 state=2；可恢复，所以不停进程）。

全部用假的任务快照与假的标准输出，不真等 15 s、不碰真迅雷库。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from urllib.parse import urlparse
from unittest import mock

from tests.backend_download._backend_download_helpers import (
    INFO_HASH,
    HandlerStub,
    bc,
    http_api,
    task_row,
    watch_download,
)

BASE = {
    "ih": INFO_HASH,
    "final_dir": "/Volumes/XIAOMI SSD2/Media/Movies/一战再战 (2025)",
    "kind": "movie",
    "series_name": "一战再战",
    "year": "2025",
    "category": "Movies",
}


def deferred_line(taskid=297, info_hash=INFO_HASH, download_dir="/tmp/staging/一战再战 (2025)"):
    return "ETA_DEFERRED " + json.dumps(
        {"taskid": taskid, "info_hash": info_hash, "download_dir": download_dir},
        ensure_ascii=False,
    )


def submit(body, stdout="", rc=0):
    """跑一次 `_api_submit`，返回 (响应体, 命令行, 落下的阶段)。"""
    stub = HandlerStub()
    cmds, stages = [], []

    def run(cmd, timeout):
        cmds.append(list(cmd))
        return rc, stdout, ""

    with mock.patch.object(http_api, "valid_target_dir", lambda path: True), \
         mock.patch.object(http_api.service, "run_cli", run), \
         mock.patch.object(http_api, "_write_stage",
                           lambda ih, stage, reason="": stages.append((ih, stage, reason))), \
         mock.patch.object(bc, "read_context", lambda slug: {}):
        http_api.ConsoleHandler._api_submit(stub, dict(body))
    kind, payload = stub.last
    assert kind == "ok", payload
    return payload, cmds[0] if cmds else [], stages


class SubmitWithoutEtaTests(unittest.TestCase):
    """① 提交不再等测速窗口；默认行为逐字不变。"""

    def test_wait_eta_false_skips_the_probe_and_reports_the_task(self):
        payload, cmd, stages = submit({**BASE, "wait_eta": False},
                                      stdout=deferred_line())
        self.assertIn("--eta-probe-seconds", cmd)
        self.assertEqual(cmd[cmd.index("--eta-probe-seconds") + 1], "0")
        self.assertNotIn("--no-watch", cmd, "wait_eta=false 默认要由提交侧直接接上监控")
        self.assertTrue(payload["eta_deferred"])
        self.assertEqual(payload["taskid"], 297)
        self.assertEqual(payload["info_hash"], INFO_HASH)
        self.assertEqual(payload["final_dir"], "/tmp/staging/一战再战 (2025)")
        self.assertEqual(payload["stage"], "monitoring")
        self.assertEqual([s[1] for s in stages], ["submitting"],
                         "提交一按下去就该有 submitting 阶段可读")

    def test_default_submit_stays_byte_identical(self):
        """不传 wait_eta：命令行与响应体都不能多出任何东西（向后兼容的硬约束）。"""
        payload, cmd, stages = submit(BASE, stdout="ETA_ACCEPTED eta=1.06h")
        # category 会在处理里改写 final_dir（bc.derive_final_dir），所以期望值也按它算
        expected_dir = bc.derive_final_dir("一战再战", "movie", "2025", "Movies")
        self.assertEqual(
            cmd,
            ["download/submit_xunlei.py", INFO_HASH, expected_dir,
             "--kind", "movie", "--eta-max-hours", "4.0", "--no-watch",
             "--series-name", "一战再战", "--year", "2025"],
        )
        self.assertEqual(set(payload), {"ok", "rc", "stdout", "stderr"})
        self.assertEqual(stages, [], "默认模式不写阶段，行为与改动前一致")

    def test_metadata_rejection_is_distinguishable_from_a_built_task(self):
        payload, _, stages = submit(
            {**BASE, "wait_eta": False},
            stdout="RESOURCE_REJECTED no readable video >=100MB found in torrent\n",
            rc=3,
        )
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["eta_deferred"], "rc=3 是元数据探测失败：没有任务、没有 ETA 可延后")
        self.assertEqual(payload["stage"], "failed_submit")
        self.assertEqual([s[1] for s in stages], ["submitting", "failed_submit"])

    def test_success_without_the_marker_is_not_reported_as_monitoring(self):
        """rc=0 但没有 ETA_DEFERRED：不知道任务建没建，按失败口径记账。"""
        payload, _, stages = submit({**BASE, "wait_eta": False}, stdout="something else")
        self.assertFalse(payload["eta_deferred"])
        self.assertEqual(payload["stage"], "failed_submit")
        self.assertIn("failed_submit", [s[1] for s in stages])


class StageEndpointTests(unittest.TestCase):
    """② 阶段状态写入与读取（只读端点，旧文件按 status 映射）。"""

    def read_stages(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            for name, payload in files.items():
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
            stub = HandlerStub()
            with mock.patch.object(http_api, "STATE_DIR", tmp), \
                 mock.patch.object(http_api, "_watch_module", lambda: watch_download):
                http_api.ConsoleHandler._get_stages(stub, urlparse("/api/stages"))
        kind, payload = stub.last
        self.assertEqual(kind, "ok")
        return payload

    def test_new_and_legacy_watch_files_are_both_reported(self):
        payload = self.read_stages({
            f"{INFO_HASH}.json": {
                "status": "running", "stage": "monitoring", "stage_reason": "",
                "stage_updated_at": "2026-10-15T12:00:00+00:00", "taskid": 297,
            },
            "AB" * 20 + ".json": {"status": "completed", "taskid": 295},
        })
        self.assertEqual(payload["count"], 2)
        fresh = payload["stages"][INFO_HASH]
        self.assertEqual(fresh["stage"], "monitoring")
        self.assertEqual(fresh["taskid"], 297)
        self.assertEqual(fresh["updated_at"], "2026-10-15T12:00:00+00:00")
        legacy = payload["stages"]["AB" * 20]
        self.assertEqual(legacy["stage"], "done", "旧文件没有 stage，要按 status 映射出阶段")
        self.assertEqual(legacy["reason"], "")

    def test_unknown_legacy_status_maps_to_empty_stage(self):
        payload = self.read_stages({"CD" * 20 + ".json": {"status": "something-new"}})
        self.assertEqual(payload["stages"]["CD" * 20]["stage"], "")

    def test_broken_json_is_skipped_instead_of_failing_the_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "EF.json"), "w", encoding="utf-8") as handle:
                handle.write("{not json")
            stub = HandlerStub()
            with mock.patch.object(http_api, "STATE_DIR", tmp), \
                 mock.patch.object(http_api, "_watch_module", lambda: watch_download):
                http_api.ConsoleHandler._get_stages(stub, urlparse("/api/stages"))
        self.assertEqual(stub.last[1]["count"], 0)


class ClassifyTaskRowTests(unittest.TestCase):
    """③④ 存活判定：任务消失 → failed_download；暂停 → paused。"""

    def test_missing_task_needs_consecutive_misses(self):
        self.assertEqual(watch_download.classify_task_row(None, 1, 2), (None, ""),
                         "一次查不到不算消失（客户端可能正在写库）")
        stage, reason = watch_download.classify_task_row(None, 2, 2)
        self.assertEqual(stage, "failed_download")
        self.assertEqual(reason, "任务已从迅雷消失")

    def test_paused_task_flips_to_paused_and_keeps_the_process_alive(self):
        stage, reason = watch_download.classify_task_row({**task_row(), "state": 2}, 0, 2)
        self.assertEqual(stage, "paused", "迅雷 state=2 要能与「消失」区分开")
        self.assertEqual(reason, "已在迅雷暂停")

    def test_thunder_error_is_a_download_failure(self):
        stage, reason = watch_download.classify_task_row({**task_row(), "state": 5}, 0, 2)
        self.assertEqual(stage, "failed_download")
        self.assertIn("state=5", reason)

    def test_running_task_keeps_its_stage(self):
        self.assertEqual(watch_download.classify_task_row(task_row(), 0, 2), (None, ""))


class StageWriteTests(unittest.TestCase):
    """阶段落盘：合并写、旧字段保留、旧文件能映射出阶段。"""

    def test_write_stage_merges_and_keeps_existing_fields(self):
        state = {"status": "running", "taskid": 297, "progress": 12.5}
        written = {}
        with mock.patch.object(watch_download, "read_watcher_state", lambda ih: dict(state)), \
             mock.patch.object(watch_download, "write_watcher_state",
                               lambda ih, payload: written.update(payload)):
            watch_download.write_stage("f0917e25" + "0" * 32, "verifying", "下载完成，开始检测归档")
        self.assertEqual(written["stage"], "verifying")
        self.assertEqual(written["stage_reason"], "下载完成，开始检测归档")
        self.assertTrue(written["stage_updated_at"])
        self.assertEqual(written["taskid"], 297, "合并写不能抹掉正在跑的字段")
        self.assertEqual(written["info_hash"], ("f0917e25" + "0" * 32).upper())

    def test_stage_for_watch_state_reads_stage_then_falls_back_to_status(self):
        self.assertEqual(watch_download.stage_for_watch_state({"stage": "paused"}), "paused")
        self.assertEqual(watch_download.stage_for_watch_state({"status": "missing"}), "failed_download")
        self.assertEqual(watch_download.stage_for_watch_state({}), "")


if __name__ == "__main__":
    unittest.main()
