"""CLI 契约与离线干跑：三个 CLI 能执行、`--help` 退出码为 0，fixtures 候选 + 临时池全程离线。

拆分自 `tests/backend_probe/test_backend_integration.py`；共享件（常量、离线守卫、边界桩、`IntegrationCase`）在 `tests/backend_probe/_backend_integration_helpers.py`。
"""
from __future__ import annotations

import os
import subprocess
import sys

from tests.backend_probe._backend_integration_helpers import (
    BACKEND,
    FIXTURES,
    ROOT,
    IntegrationCase,
)


class CliContractTests(IntegrationCase):
    """三个 CLI 必须能执行、--help 退出码为 0。"""

    def _help(self, script, *extra):
        return subprocess.run(
            [sys.executable, str(BACKEND / script), *extra, "--help"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )

    def test_probe_magnets_help(self):
        done = self._help("probe/probe_magnets.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--budget-secs", done.stdout)

    def test_search_pool_help(self):
        done = self._help("search/search_pool.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        for flag in ("--page-budget-secs", "--page-workers",
                     "--no-new-pages", "--page-errors", "--collapse-releases"):
            self.assertIn(flag, done.stdout, flag)

    def test_candidate_score_help(self):
        """candidate_score 是库不是 CLI：`--help` 被忽略并正常退出（无 argparse）。"""
        done = self._help("candidate_score.py")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_no_third_party_imports_in_the_new_module(self):
        """I2 声明的「零第三方依赖」：新模块的顶层 import 只允许标准库。"""
        source = (BACKEND / "torrents_csv_source.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if line.startswith(("import ", "from ")):
                module = line.split()[1].split(".")[0]
                self.assertIn(
                    module,
                    {"argparse", "csv", "datetime", "json", "os", "time",
                     "urllib", "typing", "math", "re"},
                    f"torrents_csv_source 引入了非标准库依赖：{line}",
                )


class OfflineDryRunTests(IntegrationCase):
    """验收要求的离线干跑：fixtures 候选 + /tmp 池，全程离线。"""

    def _dry_run(self, mock_network=True):
        pool_path = self.fresh_pool()
        args = ["--no-live-check"]
        calls = []
        mocks = self.probe_network_patchers(calls) if mock_network else []
        rc, out_path, stdout = self.run_probe(pool_path, args, mocks=mocks)
        return rc, out_path, stdout, calls

    def test_dry_run_is_fully_offline_and_ranks_the_fixture_candidates(self):
        rc, out_path, _, calls = self._dry_run()
        self.assertEqual(calls, [], "干跑必须完全离线")
        self.assertEqual(rc, 0)
        probe = self.read_json(out_path)
        self.assertTrue(probe["ranked"])
        self.assertIsNotNone(probe["best"])
        for row in probe["ranked"]:
            self.assertIn("dimensions", row)
            self.assertIn("breakdown", row)
        # 总分 = Σ(权重 × 维度)：跨模块（candidate_score 的权重 × probe 的流水线）
            weights = probe["policy"]["weights"]
            expected = sum(row["dimensions"][name] * weights[name] for name in weights)
            self.assertAlmostEqual(row["score"], expected, delta=0.05)

    def test_dry_run_as_a_subprocess_lands_in_the_allowed_exit_set(self):
        pool_path = self.fresh_pool()
        out_path = os.path.join(self.tmp, "subprocess-probe.json")
        done = subprocess.run(
            [sys.executable, str(BACKEND / "probe" / "probe_magnets.py"), "fixture",
             "--kind", "movie", "--duration-minutes", "120",
             "--candidate-file", str(FIXTURES / "candidates.txt"),
             "--pool-file", pool_path, "--json-out", out_path,
             "--workers", "2", "--no-live-check"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertIn(done.returncode, {0, 4, 5}, done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:])
        probe = self.read_json(out_path)
        self.assertTrue(probe["ranked"] and probe["best"])
        self.assertIn("dimensions", probe["ranked"][0])
        self.assertIn("breakdown", probe["ranked"][0])

    def test_probe_json_exposes_the_new_budget_and_stage_fields(self):
        """I3 的输出契约：partial/timeout_stage/expired_stages/stage_seconds/completed。"""
        rc, out_path, _, _ = self._dry_run()
        self.assertEqual(rc, 0)
        probe = self.read_json(out_path)
        for key in ("partial", "timeout_stage", "expired_stages", "stage_seconds",
                    "completed", "live_check", "budget_secs"):
            self.assertIn(key, probe, key)
        self.assertFalse(probe["partial"])
        self.assertIn("health", probe["stage_seconds"])
        self.assertGreaterEqual(probe["completed"]["metadata"], 1)
