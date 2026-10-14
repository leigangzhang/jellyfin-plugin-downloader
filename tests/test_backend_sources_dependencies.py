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
        allowed = set(sys.stdlib_module_names) | {"media_download_lib",
                                                  "torrents_csv_source",
                                                  "search_pool",
                                                  "source_config"}
        for name in ("search_pool.py", "torrents_csv_source.py", "source_config.py"):
            tree = ast.parse((BACKEND / name).read_text(encoding="utf-8"))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
            self.assertLessEqual(roots, allowed, f"{name} imports outside the stdlib: "
                                                 f"{sorted(roots - allowed)}")
