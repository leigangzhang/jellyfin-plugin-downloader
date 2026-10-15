"""`/api/submit` 的载荷契约：`trust_magnet` 逃生开关与补齐的 `year`。

Subject: `backend/console_server/http_api.py` 的 `_api_submit`。

两条实测缺陷各钉一组用例：

* 闸门只有 25s 元数据窗口，窗口内拿不到文件列表就一律 rc=3 RESOURCE_REJECTED
  （F2/F4/F5 三条链路 26.2–27.1s 全拒），没有出路 —— 所以要有 `trust_magnet`，
  而**缺省必须与现在完全一致**（命令行里不能凭空多出 `--trust-magnet`）；
* 面板提交时 `year` 可能是空串，`final_dir` 因此丢掉 `(年份)`（F2 实测
  `"year":""`）—— 所以请求没给年份时要回落到条目上下文。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.backend_download._backend_download_helpers import (
    bc,
    fake_run_cli,
    http_api,
    HandlerStub,
)


def submit(body):
    """跑一次 `_api_submit`，返回 (stub, 命令行列表)。"""
    stub = HandlerStub()
    cmds = []
    with mock.patch.object(http_api, "valid_target_dir", lambda path: True), \
         mock.patch.object(http_api.service, "run_cli", fake_run_cli(cmds)):
        http_api.ConsoleHandler._api_submit(stub, body)
    return stub, cmds


BASE = {
    "ih": "F0917E25C9220BDD79BD581A897C2CED4B22F95C",
    "final_dir": "/Volumes/XIAOMI SSD2/Media/Movies/一战再战 (2025)",
    "kind": "movie",
    "series_name": "一战再战",
    "year": "2025",
    "category": "Movies",
}


class TrustMagnetTests(unittest.TestCase):
    """逃生开关：默认不改变任何现有行为，显式打开才放行磁力直连。"""

    def test_default_does_not_pass_the_flag(self):
        body = {k: v for k, v in BASE.items() if k != "year"}
        _, cmds = submit(body)
        self.assertEqual(len(cmds), 1)
        self.assertNotIn("--trust-magnet", cmds[0],
                         "缺省 false 时命令行必须与修复前逐字一致（向后兼容）")

    def test_explicit_false_does_not_pass_the_flag(self):
        _, cmds = submit({**BASE, "trust_magnet": False})
        self.assertNotIn("--trust-magnet", cmds[0])

    def test_true_passes_the_escape_hatch(self):
        _, cmds = submit({**BASE, "trust_magnet": True})
        self.assertIn("--trust-magnet", cmds[0],
                      "trust_magnet=true 必须把逃生开关传给 submit_xunlei.py")

    def test_flag_is_the_last_argument_and_keeps_the_rest(self):
        _, cmds = submit({**BASE, "trust_magnet": True})
        cmd = cmds[0]
        self.assertEqual(cmd[0], "download/submit_xunlei.py")
        self.assertEqual(cmd[-1], "--trust-magnet")
        self.assertIn("--no-watch", cmd)
        self.assertIn(BASE["ih"], cmd)


class YearInSubmitPayloadTests(unittest.TestCase):
    """`year` 进载荷：请求优先，缺了回落到条目上下文，都没有就不编。"""

    def test_request_year_wins(self):
        _, cmds = submit({**BASE, "year": "2025"})
        cmd = cmds[0]
        self.assertIn("--year", cmd)
        self.assertEqual(cmd[cmd.index("--year") + 1], "2025")
        self.assertIn("(2025)", cmd[2])

    def test_year_falls_back_to_the_entry_context(self):
        body = {k: v for k, v in BASE.items() if k != "year"}
        reader = mock.MagicMock(return_value={"year": "2025"})
        with mock.patch.object(bc, "read_context", reader):
            _, cmds = submit(body)
        self.assertTrue(reader.called, "请求没给年份时应去读条目上下文")
        self.assertEqual(reader.call_args.args[0], bc.slugify("一战再战"))
        cmd = cmds[0]
        self.assertEqual(cmd[cmd.index("--year") + 1], "2025")
        self.assertTrue(cmd[2].endswith(os.path.join("Movies", "一战再战 (2025)")),
                        f"final_dir 必须带上 (年份)，实际 {cmd[2]!r}")

    def test_missing_everywhere_leaves_the_name_bare(self):
        body = {k: v for k, v in BASE.items() if k != "year"}
        with mock.patch.object(bc, "read_context", lambda slug: {}):
            _, cmds = submit(body)
        cmd = cmds[0]
        self.assertNotIn("--year", cmd, "两处都没有年份时不许编一个出来")
        self.assertTrue(cmd[2].endswith(os.path.join("Movies", "一战再战")),
                        f"final_dir 应为裸片名，实际 {cmd[2]!r}")

    def test_blank_request_year_is_treated_as_missing(self):
        body = {**BASE, "year": "   "}
        with mock.patch.object(bc, "read_context", lambda slug: {"year": "2025"}):
            _, cmds = submit(body)
        self.assertEqual(cmds[0][cmds[0].index("--year") + 1], "2025")

    def test_kind_is_normalized(self):
        with mock.patch.object(bc, "read_context", lambda slug: {}):
            _, cmds = submit({**BASE, "kind": " Movie ", "year": ""})
        self.assertIn("--kind", cmds[0])
        self.assertEqual(cmds[0][cmds[0].index("--kind") + 1], "movie")


if __name__ == "__main__":
    unittest.main()
