"""P0 守卫：外部工具（aria2c / ffprobe / ffmpeg）的路径解析，以及「工具缺失不能被
吞成『种子里没有可读视频』」。

背景（实测）：Jellyfin 从 Finder/`open` 启动时，插件后端继承 launchd 的最小 PATH，
没有 /opt/homebrew/bin。后端按裸名调 aria2c → FileNotFoundError(⊂OSError) 被
`except (OSError, subprocess.SubprocessError, ValueError): return []` 吞掉 → 提交
上报成 `RESOURCE_REJECTED no readable video >=100MB found in torrent`（rc=3），
耗时 105–228ms。

本模块钉三件事：
  1. 解析顺序与「注入路径不可用就报错、不静默回退」；
  2. 两个 aria2c 调用点和 ffprobe 调用点**抛 ToolMissing**，而不是返回空/None
     —— 这一条在修复前是红的（见下方各处 docstring 里的「修复前」）；
  3. 记录文件格式与环境变量名在 `install.sh` / `ToolLocator.cs` / `tool_paths.py`
     三处一致（acceptance 2 与 4 要求的那份「同一份格式」）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"

# backend/ 不是包，脚本之间靠同目录 import；测试要能加载被测模块。
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from media_download_lib import media as tlib_media  # noqa: E402
from media_download_lib import torrents as tlib_torrents  # noqa: E402
from probe import probe_magnets as pm  # noqa: E402
# 解析器住在包内 _shared.py（唯一真相源），由包的 __init__ 扁平再导出。
from media_download_lib import INSTALL_HINTS, TOOL_ENV, ToolMissing, tool_path  # noqa: E402

TOOL_LOCATOR_CS = ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "ToolLocator.cs"
INSTALL_SH = ROOT / "install.sh"

# 去掉 Homebrew 的 PATH：`/opt/homebrew/bin` 不在其中，而三件工具都在那里。
# 保留系统目录，否则 `bash` / `dirname` / `grep` 自己都找不到。
SYSTEM_PATH_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


class ToolTestCase(unittest.TestCase):
    """公共夹具：临时目录、假工具目录、PATH 操作。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name
        self.addCleanup(self._tmpdir.cleanup)

    def fake_tool_dir(self, *names):
        """建一个装着可执行假工具的目录（`sh -c exit 0`，不做事、立刻退出）。"""
        directory = os.path.join(self.tmp, "fake-tools")
        os.makedirs(directory, exist_ok=True)
        for name in names:
            path = os.path.join(directory, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        return directory

    def env_with_path(self, *dirs, **extra):
        """把 PATH 钉成 dirs + 系统目录，避免继承真实 PATH 里的 Homebrew。"""
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(list(dirs) + list(SYSTEM_PATH_DIRS))
        for name in TOOL_ENV.values():
            env.pop(name, None)
        env.update(extra)
        return env


class InjectedPathTests(ToolTestCase):
    """解析顺序：JMD_*_PATH 优先，且它坏了要报错而不是回退。"""

    def test_injected_path_wins_over_path_lookup(self):
        injected = os.path.join(self.fake_tool_dir("aria2c"), "aria2c")
        with mock.patch.dict(os.environ, {"JMD_ARIA2C_PATH": injected}, clear=False):
            self.assertEqual(tool_path("aria2c"), injected)

    def test_bogus_injected_path_raises_instead_of_falling_back_to_path(self):
        """注入路径指向不存在的东西时抛 ToolMissing —— 即使 PATH 上有同名命令。

        回退到 PATH 会让「记录文件里那行坏了」变成一个查不出来的隐性状态；
        这里把它钉成显式错误。
        """
        fake = self.fake_tool_dir("aria2c")
        bogus = os.path.join(self.tmp, "no-such-aria2c")
        env = self.env_with_path(fake, JMD_ARIA2C_PATH=bogus)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ToolMissing) as raised:
                tool_path("aria2c")
        self.assertEqual(raised.exception.name, "aria2c")
        self.assertIn(bogus, str(raised.exception))

    def test_missing_everywhere_raises_with_install_hint(self):
        env = self.env_with_path(self.fake_tool_dir(), JMD_FFPROBE_PATH="")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ToolMissing) as raised:
                tool_path("ffprobe")
        self.assertEqual(raised.exception.name, "ffprobe")
        self.assertEqual(raised.exception.install_hint, INSTALL_HINTS["ffprobe"])
        self.assertIn("brew install ffmpeg", str(raised.exception))

    def test_injected_path_must_be_executable(self):
        not_executable = os.path.join(self.tmp, "aria2c")
        with open(not_executable, "w", encoding="utf-8") as handle:
            handle.write("not executable\n")
        os.chmod(not_executable, 0o644)
        fake = self.fake_tool_dir("aria2c")
        env = self.env_with_path(fake, JMD_ARIA2C_PATH=not_executable)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ToolMissing):
                tool_path("aria2c")

    def test_unknown_tool_name_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            tool_path("curl")


class ProbeToolMissingTests(ToolTestCase):
    """回归守卫：工具缺失必须是「工具缺失类错误」，不是空结果。

    这三条在修复前是红的：旧代码回退到 PATH 上的假工具（或裸名），
    拿到空结果后 return None / []，上层于是把它当成「种子里没有可读视频」。
    """

    def test_fetch_metadata_reports_tool_missing_not_none(self):
        """修复前：返回 None（⇒ 候选被判 'no readable video file >=100MB'）。"""
        fake = self.fake_tool_dir("aria2c")
        bogus = os.path.join(self.tmp, "no-such-aria2c")
        env = self.env_with_path(fake, JMD_ARIA2C_PATH=bogus)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ToolMissing) as raised:
                pm.fetch_metadata("0" * 40, 1)
        self.assertEqual(raised.exception.name, "aria2c")

    def test_probe_torrent_files_reports_tool_missing_not_empty_list(self):
        """修复前：返回 []（⇒ submit 打出 RESOURCE_REJECTED 并 rc=3）。"""
        fake = self.fake_tool_dir("aria2c")
        bogus = os.path.join(self.tmp, "no-such-aria2c")
        env = self.env_with_path(fake, JMD_ARIA2C_PATH=bogus)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ToolMissing) as raised:
                tlib_torrents.probe_torrent_files("1" * 40, timeout=1)
        self.assertEqual(raised.exception.name, "aria2c")

    def test_probe_media_reports_tool_missing_not_file_not_found(self):
        """修复前：抛 FileNotFoundError（⊂OSError），正好被上层的 OSError 兜底接走。"""
        fake = self.fake_tool_dir("ffprobe")
        bogus = os.path.join(self.tmp, "no-such-ffprobe")
        env = self.env_with_path(fake, JMD_FFPROBE_PATH=bogus)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ToolMissing):
                tlib_media.probe_media("/tmp/whatever.mkv")

    def test_decode_check_names_the_missing_tool(self):
        """decode_check 返回可区分的文案，而不是「ffmpeg failed to start: [Errno 2]」。"""
        fake = self.fake_tool_dir("ffmpeg")
        bogus = os.path.join(self.tmp, "no-such-ffmpeg")
        env = self.env_with_path(fake, JMD_FFMPEG_PATH=bogus)
        with mock.patch.dict(os.environ, env, clear=True):
            ok, reason, _ = tlib_media.decode_check("/tmp/whatever.mkv", timeout=5)
        self.assertFalse(ok)
        self.assertIn("tool missing", reason)
        self.assertIn("ffmpeg", reason)
        self.assertIn("brew install ffmpeg", reason)


class RecordedPathContractTests(ToolTestCase):
    """记录文件：格式与工具名在 install.sh / ToolLocator.cs / tool_paths.py 三处一致。"""

    def test_install_sh_writes_a_record_tool_locator_can_read(self):
        """跑 install.sh 的记录写入模式（只写 JSON，不 build、不重启 Jellyfin）。

        PATH 里只放三个假工具：记录内容因此与这台机器装没装 aria2c 无关。
        """
        self.assertTrue(INSTALL_SH.exists(), INSTALL_SH)
        dest = os.path.join(self.tmp, "plugin-dir")
        os.makedirs(dest)
        fake = self.fake_tool_dir("aria2c", "ffprobe", "ffmpeg")
        env = self.env_with_path(fake)
        process = subprocess.run(
            ["bash", str(INSTALL_SH), "--record-tools-only", dest],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
            timeout=120,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

        record_path = os.path.join(dest, "backend-tools.json")
        self.assertTrue(os.path.exists(record_path), process.stdout + process.stderr)
        record = json.loads(Path(record_path).read_text(encoding="utf-8"))

        self.assertIsInstance(record.get("version"), int)
        tools = record.get("tools")
        self.assertIsInstance(tools, dict)
        # 三个工具都记到了，且就是 PATH 上那三个假货（说明逐一解析、逐一记录）
        self.assertEqual(set(tools), set(TOOL_ENV))
        for name, path in tools.items():
            self.assertTrue(os.path.isabs(path), (name, path))
            self.assertEqual(path, os.path.join(fake, name))
            self.assertTrue(os.access(path, os.X_OK))

    def test_install_sh_exits_nonzero_when_tools_missing_and_brew_unavailable(self):
        """brew 不可用时明确报错并以非 0 退出，不静默继续。

        `JMD_TOOL_PREFIXES` 指到空目录 = 装作这台机器没有 Homebrew（否则
        install.sh 会按解析顺序在 /opt/homebrew/bin 下找到真工具，这条就没法确定地红）。
        """
        dest = os.path.join(self.tmp, "plugin-dir-missing")
        os.makedirs(dest)
        empty = os.path.join(self.tmp, "empty-bin")
        os.makedirs(empty)
        env = self.env_with_path(empty, JMD_TOOL_PREFIXES=empty)
        process = subprocess.run(
            ["bash", str(INSTALL_SH), "--record-tools-only", dest],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
            timeout=120,
            check=False,
        )
        output = process.stdout + process.stderr
        self.assertNotEqual(process.returncode, 0, output)
        self.assertIn("brew", output)
        for name in TOOL_ENV:
            self.assertIn(name, output)
        self.assertFalse(
            os.path.exists(os.path.join(dest, "backend-tools.json")),
            "工具没齐时不应留下记录文件",
        )

    def test_tool_locator_reads_the_same_record_and_env_names(self):
        """C# 侧读的必须是同一份格式：文件名、三个工具名、三个环境变量名逐字相同。"""
        self.assertTrue(TOOL_LOCATOR_CS.exists(), TOOL_LOCATOR_CS)
        source = TOOL_LOCATOR_CS.read_text(encoding="utf-8")
        self.assertIn('"backend-tools.json"', source)
        self.assertIn('"tools"', source)
        for name, env_name in TOOL_ENV.items():
            self.assertIn(f'"{name}"', source, f"ToolLocator 里缺少工具名 {name}")
            self.assertIn(f'"{env_name}"', source, f"ToolLocator 里缺少环境变量 {env_name}")


if __name__ == "__main__":
    unittest.main()
