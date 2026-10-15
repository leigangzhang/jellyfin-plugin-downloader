"""A10（后端侧）：`backend/trending/` 只用标准库、无 `__file__` 拼路径、无机器相关绝对路径。

对着**源码文本**断言：这几条约束一旦被违反，代码本身仍然跑得起来，只有换一台机器
才会暴露 —— 所以它们必须在每次提交时被读一遍源码。

白名单只有标准库 + `media_download_lib`（数据根的唯一出处）。包内互相引用一律用
相对 import（`from .trending_source import ...`），所以正则会跳过它们：相对 import
按定义就在包内，不需要白名单。
"""

from __future__ import annotations

import re
import sys
import unittest

from tests.backend_trending import BACKEND

TRENDING_DIR = BACKEND / "trending"
ALLOWED_NON_STDLIB = {"media_download_lib"}
MACHINE_PATH_MARKERS = ("/Users/", "/home/", "/Volumes/", "/private/", "/tmp/", "C:\\")
ABSOLUTE_PATH_RE = re.compile(
    r"""["'](?:[A-Za-z]:[\\/]|/(?:Users|home|Volumes|private|tmp|var|opt|usr|Applications)/)"""
)
IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))",
    re.MULTILINE,
)


def stdlib_names():
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return set(names)
    # 3.9 及更早没有 stdlib_module_names：退回本包真正用到的那几个
    return {
        "contextlib",
        "json",
        "os",
        "re",
        "tempfile",
        "time",
        "urllib",
    }


class TrendingSourceHygieneTests(unittest.TestCase):
    def setUp(self):
        self.sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(TRENDING_DIR.glob("*.py"))
        }

    def test_the_package_has_the_documented_files(self):
        self.assertEqual(
            sorted(self.sources),
            ["__init__.py", "trending_cache.py", "trending_source.py", "trending_watchlist.py"],
        )

    def test_no_module_builds_paths_from_dunder_file(self):
        for name, source in self.sources.items():
            self.assertNotIn("__file__", source, name)

    def test_no_machine_dependent_absolute_paths(self):
        for name, source in self.sources.items():
            for marker in MACHINE_PATH_MARKERS:
                self.assertNotIn(marker, source, f"{name}: {marker}")
            self.assertIsNone(ABSOLUTE_PATH_RE.search(source), name)

    def test_imports_are_standard_library_or_the_documented_whitelist(self):
        allowed = stdlib_names() | ALLOWED_NON_STDLIB
        seen = set()
        for name, source in self.sources.items():
            for match in IMPORT_RE.finditer(source):
                clause = match.group(1) or match.group(2) or ""
                for part in clause.split(","):
                    root = part.strip().split(".")[0]
                    if not root:
                        continue
                    seen.add(root)
                    self.assertIn(root, allowed, f"{name}: 非白名单 import {root}")
        # 白名单不是空的，也不是「什么 import 都没有」：真读过这些模块
        self.assertIn("urllib", seen)
        self.assertIn("media_download_lib", seen)

    def test_the_data_root_comes_from_media_download_lib_only(self):
        self.assertIn("from media_download_lib import DATA_ROOT", self.sources["trending_cache.py"])
        self.assertIn(
            "from media_download_lib import DATA_ROOT", self.sources["trending_watchlist.py"]
        )
        # 谁都不许自己再算一份数据根
        for name, source in self.sources.items():
            self.assertNotIn("expanduser", source, name)


if __name__ == "__main__":
    unittest.main()
