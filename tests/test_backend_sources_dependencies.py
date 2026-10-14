"""The backend stays zero-dependency.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import ast
import sys
import unittest

from tests._backend_sources_helpers import BACKEND


class DependencyTests(unittest.TestCase):
    """The backend stays zero-dependency."""
    def test_backend_modules_import_stdlib_only(self):
        """backend/ 下每个模块只许 import 标准库，或 import 同目录的另一个模块。

        判据刻意不写死文件名单：名单会随着拆文件悄悄缩小扫描面。2026-10-14
        `search_pool.py` 拆成 7 个文件后，68% 的字节（53722/79021 B）一度落在
        守卫之外 —— 往 `search_pool_api.py` 里塞 `import requests` 是绿的，只有写在
        `search_pool.py` 上才会红。改成「全目录 + 同目录模块自动放行」之后，
        拆文件、改名、新增模块都不会再让覆盖静默失效。
        """
        local_modules = {path.stem for path in BACKEND.glob("*.py")}
        allowed = set(sys.stdlib_module_names) | local_modules
        scanned = sorted(BACKEND.glob("*.py"))
        self.assertGreaterEqual(
            len(scanned), 10,
            f"backend 模块数量异常（{len(scanned)}），扫描面疑似塌陷：{scanned}")
        for path in scanned:
            name = path.name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
            # 只报差集：assertLessEqual 会把整份 allowed（200+ 项标准库名）打进失败输出。
            self.assertEqual(
                sorted(roots - allowed), [],
                f"{name} imports outside the stdlib")
