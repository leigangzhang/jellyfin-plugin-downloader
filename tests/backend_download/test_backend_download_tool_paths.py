"""P0 补覆盖：`backend/download/verify_media.py` 的 ffmpeg 调用走同一套工具解析。

背景：t22 只把 `media_download_lib/*` 与 `probe/probe_metadata.py` 的裸名调用改成了
按 `JMD_*_PATH` → PATH 解析，`backend/download/verify_media.py` 当时不在范围内，
仍写着 `subprocess.run(["ffmpeg", ...])`。在 launchd 最小 PATH 下（Finder/open 启动
Jellyfin）这一步会以 `FileNotFoundError`（⊂ OSError）收场，与「解码验证不通过」
分不出来 —— 而这两个函数的返回值本来是给「验证结果」用的。

本模块钉两件事：
  1. 注入 `JMD_FFMPEG_PATH` 指向不可用路径时，得到的是 `ToolMissing`，不是
     「拿 PATH 上的同名命令凑合跑」也不是「验证不通过」；
  2. 正常路径下 argv 与修复前逐字一致（只换 argv[0] 的来源），行为向后兼容。

路径：本模块自己把 `backend/` 塞进 sys.path，不 import 同组的
`_backend_download_helpers`（那个共享件会连带 import submit_xunlei / http_api /
source_config，而这几份正被其他成员并行修改；守卫不该被别人的在途改动牵连）。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
VERIFY_MEDIA = BACKEND / "download" / "verify_media.py"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from download import verify_media  # noqa: E402
from media_download_lib import ToolMissing  # noqa: E402

# 去掉 Homebrew 的 PATH：三个工具都在 /opt/homebrew/bin，保留系统目录让 bash/ffmpeg
# 之外的命令仍可用。用例把假工具放进这个 PATH，用来证明「不会回退到裸名」。
SYSTEM_PATH_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

# verify_media 里不允许再出现的裸名外部工具（argv[0] 必须是解析出来的绝对路径）。
BARE_TOOL_NAMES = {"ffmpeg", "ffprobe", "aria2c"}


class VerifyMediaToolTestCase(unittest.TestCase):
    """公共夹具：临时目录、假工具、PATH/JMD_* 操作。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name
        self.addCleanup(self._tmpdir.cleanup)

    def fake_tool_dir(self, *names):
        """假工具：不做事、立刻退出 0，且把 argv 记到 `<dir>/<name>.argv`。"""
        directory = os.path.join(self.tmp, "fakebin")
        os.makedirs(directory, exist_ok=True)
        for name in names:
            path = os.path.join(directory, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{path}.argv"\nexit 0\n')
            os.chmod(path, 0o755)
        return directory

    def env(self, *dirs, **extra):
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(list(dirs) + list(SYSTEM_PATH_DIRS))
        for name in ("JMD_ARIA2C_PATH", "JMD_FFPROBE_PATH", "JMD_FFMPEG_PATH"):
            env.pop(name, None)
        env.update(extra)
        return env


class ToolMissingTests(VerifyMediaToolTestCase):
    """反例守卫：ffmpeg 解析不到就必须抛 ToolMissing。

    修复前这三条都红：`JMD_FFMPEG_PATH` 被完全忽略，代码直接拿裸名 `ffmpeg` 跑，
    于是 PATH 上那个假 ffmpeg 被真的执行了，函数返回一个「样本都 ok」的结果。
    """

    def bogus_env(self):
        fake = self.fake_tool_dir("ffmpeg")
        return self.env(fake, JMD_FFMPEG_PATH=os.path.join(self.tmp, "no-such-ffmpeg"))

    def test_sampled_decode_layer_raises_tool_missing(self):
        with mock.patch.dict(os.environ, self.bogus_env(), clear=True):
            with self.assertRaises(ToolMissing) as raised:
                verify_media.sampled_decode_layer("/tmp/movie.mkv", 120.0)
        self.assertEqual(raised.exception.name, "ffmpeg")

    def test_hard_subtitle_layer_raises_tool_missing(self):
        with mock.patch.dict(os.environ, self.bogus_env(), clear=True):
            with self.assertRaises(ToolMissing) as raised:
                verify_media.hard_subtitle_layer("/tmp/movie.mkv", 120.0)
        self.assertEqual(raised.exception.name, "ffmpeg")

    def test_verify_file_surfaces_tool_missing_not_a_failed_verdict(self):
        """走公开入口 `verify_file`：得到的是工具缺失，而不是「验证不通过」的结果字典。"""
        with mock.patch.dict(os.environ, self.bogus_env(), clear=True), \
             mock.patch.object(verify_media, "verify_video", return_value={"ok": True}), \
             mock.patch.object(verify_media, "stream_layer", return_value={"duration": 120.0}):
            with self.assertRaises(ToolMissing):
                verify_media.verify_file("/tmp/movie.mkv")

    def test_missing_tool_is_not_an_oserror_subclass_dodge(self):
        """ToolMissing 必须能穿过「只捕 OSError」的兜底：它不是 OSError 的子类。"""
        self.assertFalse(issubclass(ToolMissing, OSError))
        self.assertTrue(issubclass(ToolMissing, RuntimeError))

    def test_no_bare_name_tool_calls_remain(self):
        """源码级守卫：subprocess.run 的 argv[0] 不允许是裸名工具字面量。"""
        tree = ast.parse(VERIFY_MEDIA.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if getattr(node.func, "attr", None) != "run":
                continue
            first = node.args[0]
            if not isinstance(first, ast.List) or not first.elts:
                continue
            head = first.elts[0]
            if isinstance(head, ast.Constant) and head.value in BARE_TOOL_NAMES:
                offenders.append((node.lineno, head.value))
        self.assertEqual(
            offenders, [],
            f"verify_media.py 仍在裸名调用外部工具：{offenders}（应改走 tool_path(...)）",
        )


class BackwardCompatibilityTests(VerifyMediaToolTestCase):
    """正常路径：只换 argv[0] 的来源，其余 argv 与返回值形状逐字不变。"""

    def capture(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append((list(cmd), dict(kwargs)))
            return subprocess.CompletedProcess(cmd, 0, stdout=b"\x00" * 10, stderr="")

        return captured, fake_run

    def test_sampled_decode_argv_is_unchanged_apart_from_the_binary(self):
        fake_dir = self.fake_tool_dir("ffmpeg")
        binary = os.path.join(fake_dir, "ffmpeg")
        captured, fake_run = self.capture()
        with mock.patch.dict(os.environ, self.env(fake_dir, JMD_FFMPEG_PATH=binary), clear=True), \
             mock.patch.object(verify_media.subprocess, "run", side_effect=fake_run):
            result = verify_media.sampled_decode_layer("/tmp/movie.mkv", 120.0)

        self.assertEqual(len(captured), 3)
        for cmd, kwargs in captured:
            # argv[0] 是解析到的绝对路径（修复前这里是裸名 "ffmpeg"）
            self.assertEqual(cmd[0], binary)
            self.assertEqual(
                cmd[1:],
                [
                    "-v", "error",
                    "-ss", f"{max(120.0 * [0.05, 0.50, 0.90][captured.index((cmd, kwargs))], 0):.3f}",
                    "-i", "/tmp/movie.mkv",
                    "-t", "20",
                    "-f", "null", "-",
                ],
            )
            self.assertEqual(kwargs["timeout"], 60)
            self.assertFalse(kwargs["check"])
        # 返回值形状与修复前一致：三个样本、ok 由 returncode/errors 决定
        self.assertEqual(result["ok"], True)
        self.assertEqual([s["position"] for s in result["samples"]], [6.0, 60.0, 108.0])
        self.assertEqual([s["errors"] for s in result["samples"]], [[], [], []])

    def test_hard_subtitle_argv_is_unchanged_apart_from_the_binary(self):
        fake_dir = self.fake_tool_dir("ffmpeg")
        binary = os.path.join(fake_dir, "ffmpeg")
        captured, fake_run = self.capture()
        with mock.patch.dict(os.environ, self.env(fake_dir, JMD_FFMPEG_PATH=binary), clear=True), \
             mock.patch.object(verify_media.subprocess, "run", side_effect=fake_run):
            result = verify_media.hard_subtitle_layer("/tmp/movie.mkv", 100.0)

        self.assertEqual(len(captured), 3)
        cmd, kwargs = captured[0]
        self.assertEqual(cmd[0], binary)
        self.assertEqual(
            cmd[1:],
            [
                "-v", "error",
                "-ss", "10.000",
                "-i", "/tmp/movie.mkv",
                "-frames:v", "1",
                "-vf", "crop=iw:ih*0.12:0:ih*0.88,format=gray",
                "-f", "rawvideo",
                "-pix_fmt", "gray",
                "-",
            ],
        )
        self.assertEqual(kwargs["timeout"], 60)
        self.assertFalse(result["suspected"])
        self.assertEqual(result["max_white_ratio"], 0.0)

    def test_timeout_still_yields_the_same_sample_record(self):
        """超时路径（修复前就有的兜底）逐字保持：不因为这次改动变成抛异常。"""
        fake_dir = self.fake_tool_dir("ffmpeg")
        binary = os.path.join(fake_dir, "ffmpeg")

        def timeout_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

        with mock.patch.dict(os.environ, self.env(fake_dir, JMD_FFMPEG_PATH=binary), clear=True), \
             mock.patch.object(verify_media.subprocess, "run", side_effect=timeout_run):
            result = verify_media.sampled_decode_layer("/tmp/movie.mkv", 120.0)

        self.assertFalse(result["ok"])
        self.assertEqual(
            [s["errors"] for s in result["samples"]],
            [["decode sample timed out"]] * 3,
        )


class RealFfmpegUnderMinimalPathTests(VerifyMediaToolTestCase):
    """真实环境核对：注入路径生效时，最小 PATH 下也能真的跑起来（非 mock）。"""

    def test_injected_ffmpeg_runs_with_no_homebrew_on_path(self):
        real = None
        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if os.access(candidate, os.X_OK):
                real = candidate
                break
        if real is None:
            self.skipTest("本机没有真实 ffmpeg，跳过真实执行核对")

        # 先确认：最小 PATH 下裸名根本找不到 ffmpeg
        minimal = self.env(os.path.join(self.tmp, "emptybin"))
        os.makedirs(os.path.join(self.tmp, "emptybin"), exist_ok=True)
        with mock.patch.dict(os.environ, minimal, clear=True):
            self.assertIsNone(__import__("shutil").which("ffmpeg"))
            with self.assertRaises(ToolMissing):
                verify_media.sampled_decode_layer("/tmp/definitely-missing.mkv", 1.0)
            # 注入路径后，工具被找到（文件不存在是 ffmpeg 自己的报错，不是工具缺失）
            os.environ["JMD_FFMPEG_PATH"] = real
            result = verify_media.sampled_decode_layer("/tmp/definitely-missing.mkv", 1.0)
        self.assertEqual(len(result["samples"]), 3)
        self.assertFalse(result["ok"])
        self.assertTrue(all(s["returncode"] != 0 for s in result["samples"]))


if __name__ == "__main__":
    unittest.main()
