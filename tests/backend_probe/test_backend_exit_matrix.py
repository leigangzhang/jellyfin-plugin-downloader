"""退出码矩阵：0/4/5/7/8 五个退出码在离线条件下逐一复现，语义未被集成带走。

拆分自 `tests/backend_probe/test_backend_integration.py`；共享件（常量、离线守卫、边界桩、`IntegrationCase`）在 `tests/backend_probe/_backend_integration_helpers.py`。
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.backend_probe._backend_integration_helpers import (
    EXIT_CODES,
    FIXTURES,
    IntegrationCase,
    pm,
)


class ExitCodeMatrixTests(IntegrationCase):
    """0/4/5/7/8 五个退出码在离线条件下逐一复现，语义未被集成带走。"""

    def _argv(self, extra):
        return [
            "--kind", "movie", "--duration-minutes", "120",
            "--candidate-file", str(FIXTURES / "candidates.txt"), *extra,
        ]

    def test_zero_best_result(self):
        rc, out_path, stdout = self.run_probe(self.fresh_pool(), ["--no-live-check"])
        self.assertEqual(rc, EXIT_CODES["best"])
        self.assertIn("BEST_RESULT_JSON", stdout)
        self.assertIsNotNone(self.read_json(out_path)["best"])

    def test_four_no_candidates(self):
        empty = os.path.join(self.tmp, "empty.txt")
        Path(empty).write_text("# no candidates\n", encoding="utf-8")
        out_path = os.path.join(self.tmp, "no-cand.json")
        argv = ["probe_magnets.py", "x", "--kind", "movie", "--candidate-file", empty,
                "--no-pool", "--json-out", out_path, "--workers", "2"]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(buf):
            rc = pm.main()
        self.assertEqual(rc, EXIT_CODES["no_candidate"])
        self.assertIn("NO_CANDIDATES", buf.getvalue())

    def test_five_below_min_score(self):
        rc, _, stdout = self.run_probe(
            self.fresh_pool(), ["--no-live-check", "--min-score", "101"]
        )
        self.assertEqual(rc, EXIT_CODES["below_min_score"])
        self.assertIn("BEST_BELOW_MIN_SCORE", stdout)

    def test_seven_metadata_failed_with_external_peers(self):
        """冷池 + 抓不到 metadata + announce 仍有外部 peer → 网络问题，退出码 7。"""
        health = {
            "tracker": "t", "seeders": 50, "leechers": 5, "peers": 50,
            "external_peers": 50, "own_peers": 0, "own_ips": [],
        }
        mocks = [
            mock.patch.object(pm, "announce_health", lambda *a, **k: dict(health)),
            mock.patch.object(pm, "fetch_metadata", lambda *a, **k: None),
            mock.patch.object(pm, "public_ip", lambda: ""),
        ]
        rc, _, stdout = self.run_probe(
            self.fresh_pool(cold=True), ["--no-live-check"], mocks=mocks
        )
        self.assertEqual(rc, EXIT_CODES["metadata_failed"])
        self.assertIn("METADATA_FAILED", stdout)

    def test_eight_best_is_dead_swarm(self):
        rc, _, stdout = self.run_probe(
            self.fresh_pool(external_peers=0), ["--no-live-check"]
        )
        self.assertEqual(rc, EXIT_CODES["swarm_dead"])
        self.assertIn("BEST_DEAD_SWARM", stdout)
