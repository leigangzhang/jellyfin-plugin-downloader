"""共享件：`probe_magnets` 离线契约测试的路径常量、网络桩与基类。

这里没有任何用例。`_` 前缀的文件不会被 `unittest discover` 收集（它只收
`test*.py`），所以 `ProbeCase` 这类 TestCase 基类可以安全地放在这里，而不会
让用例被重复计数。各兄弟 `test_probe_*.py` 用绝对包路径导入本模块。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
FIXTURES = ROOT / "tests" / "fixtures" / "jmd_probe"

# backend/ 不是包，脚本之间靠同目录 import；这里补上路径，让测试能加载被测模块。
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from probe import probe_magnets as pm  # noqa: E402

# 预算断言的容差：只覆盖解释器启动与调度抖动，不足以掩盖一次真实网络调用
# （metadata 实测 6.2–8.4s，announce 0.5–0.9s）。
BUDGET_SLACK_SECONDS = 2.0


def offline_network_guard(test_case):
    """把每个网络入口换成「被调用即失败」的桩，返回 patch 列表。"""
    calls = []

    def boom(name):
        def _inner(*args, **kwargs):
            calls.append(name)
            test_case.fail(f"离线测试里发生了真实网络调用：{name}{args!r}")
        return _inner

    return [
        mock.patch.object(pm, "announce_health", boom("announce_health")),
        mock.patch.object(pm, "tracker_health", boom("tracker_health")),
        mock.patch.object(pm, "fetch_metadata", boom("fetch_metadata")),
        mock.patch.object(pm, "public_ip", boom("public_ip")),
    ]


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jmd-probe-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = time.time()

    def pool_file(self, fresh_health=True, fresh_metadata=True):
        """把夹具 pool 拷进临时目录，按需把 TTL 字段写成「刚过期一点点」。

        `fresh_metadata=False` 模拟冷池：没有 metadata_at → metadata 阶段必须联网。
        """
        payload = json.loads((FIXTURES / "pool.json").read_text(encoding="utf-8"))
        for entry in payload["entries"].values():
            entry["metadata_at"] = self.now - 60 if fresh_metadata else 0
            entry["health_at"] = self.now - 60 if fresh_health else 0
        path = os.path.join(self.tmp, "pool.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return path

    def run_probe(self, pool_path, extra_args=(), argv_patches=None):
        out_path = os.path.join(self.tmp, "probe.json")
        argv = [
            "probe_magnets.py", "fixture",
            "--kind", "movie",
            "--duration-minutes", "120",
            "--candidate-file", str(FIXTURES / "candidates.txt"),
            "--pool-file", pool_path,
            "--json-out", out_path,
            "--workers", "2",
            *extra_args,
        ]
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(stdout):
            rc = pm.main()
        return rc, out_path, stdout.getvalue()

    def read_json(self, path):
        self.assertTrue(os.path.exists(path), f"{path} 不存在")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def assert_no_json_staging_left(self, pool_path):
        for name in os.listdir(self.tmp):
            self.assertFalse(
                name.endswith(".tmp"),
                f"原子写的临时文件没被 os.replace 收走：{name}",
            )
        self.assertEqual(sorted(json.loads(Path(pool_path).read_text(encoding="utf-8"))),
                         ["entries", "query", "updated_at", "version"])



FIXTURE_POOL = FIXTURES / "pool.json"
FIXTURE_POOL_NOTE = "_fixture_note"

USABLE_METADATA = {
    "name": "Fixture.Movie.Alpha.2024.1080p.WEB-DL.x264-TEST.mkv",
    "size_gb": 7.5,
    "nfiles": 1,
    "videos": [
        {
            "index": 0,
            "path": "Fixture.Movie.Alpha.2024.1080p.WEB-DL.x264-TEST.mkv",
            "name": "Fixture.Movie.Alpha.2024.1080p.WEB-DL.x264-TEST.mkv",
            "size": 8053063680,
        }
    ],
    "main_video_gb": 7.5,
    "main_video_ext": ".mkv",
    "junk_files": 0,
}
UNUSABLE_METADATA = {
    "name": "Nice.Name.But.No.Usable.Video.2024",
    "size_gb": 0.0,
    "nfiles": 3,
    "videos": [],
    "main_video_gb": 0.0,
    "main_video_ext": "",
    "junk_files": 2,
}


def console_module():
    """懒加载 console_server：只在需要控制台语义的用例里导入。

    导入它只建立常量与锁（服务在 `main()` 里才起），但它是整个控制台栈的入口，
    所以仍然推迟到用例内部，别拖累只想跑 probe 的部分。
    """
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import console_server
    return console_server

