"""`/api/watch` 的载荷契约：`year` 与 `/api/submit` 同源（同一个 `resolve_year`）。

Subject: `backend/console_server/http_api.py` 的 `_api_watch`。

t24（P1b）实测：`/api/submit` 已经会回落到条目上下文取年份，`/api/watch` 没有 ——
面板给 watch 传空 `year` 时，watcher 的监控/归档目录仍会丢 `(年份)`，与 submit 的
落点对不上。这组用例钉三件事：

* 请求 `year` 为空 → 用条目上下文里的年份，目录带 `(年份)`；
* 两处都没有 → 不报错、不编年份，退回裸片名（修复前的行为）；
* 这条回退走的是**同一个** `resolve_year`，不是复制出来的第二份逻辑 —— 换掉
  `http_api.resolve_year` 必须能同时改变 watch 的行为。
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


def watch(body):
    """跑一次 `_api_watch`，返回 (stub, 命令行列表)。"""
    stub = HandlerStub()
    cmds = []
    with mock.patch.object(http_api, "valid_target_dir", lambda path: True), \
         mock.patch.object(http_api.service, "run_cli", fake_run_cli(cmds)):
        http_api.ConsoleHandler._api_watch(stub, body)
    return stub, cmds


BASE = {
    "ih": "F0917E25C9220BDD79BD581A897C2CED4B22F95C",
    "final_dir": "/Volumes/XIAOMI SSD2/Media/Movies/一战再战 (2025)",
    "kind": "movie",
    "series_name": "一战再战",
    "year": "2025",
    "category": "Movies",
}


class WatchYearTests(unittest.TestCase):
    """`year` 进 watcher 载荷：请求优先，缺了回落到条目上下文，都没有就不编。"""

    def test_request_year_wins(self):
        _, cmds = watch(BASE)
        cmd = cmds[0]
        self.assertEqual(cmd[cmd.index("--year") + 1], "2025")
        self.assertTrue(cmd[2].endswith(os.path.join("Movies", "一战再战 (2025)")),
                        f"final_dir 必须带上 (年份)，实际 {cmd[2]!r}")

    def test_year_falls_back_to_the_entry_context(self):
        body = {k: v for k, v in BASE.items() if k != "year"}
        reader = mock.MagicMock(return_value={"year": "2025"})
        with mock.patch.object(bc, "read_context", reader):
            _, cmds = watch(body)
        self.assertTrue(reader.called, "请求没给年份时应去读条目上下文")
        self.assertEqual(reader.call_args.args[0], bc.slugify("一战再战"))
        cmd = cmds[0]
        self.assertEqual(cmd[cmd.index("--year") + 1], "2025")
        self.assertTrue(cmd[2].endswith(os.path.join("Movies", "一战再战 (2025)")),
                        f"监控目录必须带上 (年份)，实际 {cmd[2]!r}")

    def test_blank_request_year_is_treated_as_missing(self):
        body = {**BASE, "year": "   "}
        with mock.patch.object(bc, "read_context", lambda slug: {"year": "2025"}):
            _, cmds = watch(body)
        cmd = cmds[0]
        self.assertEqual(cmd[cmd.index("--year") + 1], "2025")
        self.assertTrue(cmd[2].endswith(os.path.join("Movies", "一战再战 (2025)")))

    def test_missing_everywhere_leaves_the_name_bare(self):
        body = {k: v for k, v in BASE.items() if k != "year"}
        with mock.patch.object(bc, "read_context", lambda slug: {}):
            stub, cmds = watch(body)
        self.assertEqual(stub.last[0], "ok", "取不到年份不是错误，必须照常起 watcher")
        cmd = cmds[0]
        self.assertNotIn("--year", cmd, "两处都没有年份时不许编一个出来")
        self.assertTrue(cmd[2].endswith(os.path.join("Movies", "一战再战")),
                        f"final_dir 应为裸片名，实际 {cmd[2]!r}")

    def test_watch_reuses_the_shared_resolver(self):
        """「同源」本身：换掉 resolve_year 必须同时改变 watch 的行为。

        如果 watch 里有第二份自己写的回退（哪怕行为暂时一致），这条会红。
        """
        body = {k: v for k, v in BASE.items() if k != "year"}
        resolver = mock.MagicMock(return_value="1999")
        with mock.patch.object(http_api, "resolve_year", resolver):
            _, cmds = watch(body)
        self.assertTrue(resolver.called, "_api_watch 必须调用共用的 resolve_year")
        self.assertEqual(resolver.call_args.args[0], body)
        self.assertEqual(resolver.call_args.args[1], "一战再战")
        cmd = cmds[0]
        self.assertEqual(cmd[cmd.index("--year") + 1], "1999")
        self.assertTrue(cmd[2].endswith(os.path.join("Movies", "一战再战 (1999)")),
                        f"共用解析器的返回值必须真的进目录，实际 {cmd[2]!r}")


if __name__ == "__main__":
    unittest.main()
