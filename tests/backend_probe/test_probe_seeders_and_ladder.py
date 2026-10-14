"""external_seeders 走到打分的端到端路径，与 L1 关键字阶梯的预算上界。

拆自 `tests/backend_probe/test_backend_probe.py`（按主题），共享件在
`tests/backend_probe/_backend_probe_helpers.py`。"""

from __future__ import annotations

import json
import os
import unittest

from tests.backend_probe._backend_probe_helpers import (
    BACKEND,
    FIXTURE_POOL,
    ProbeCase,
    offline_network_guard,
)

class ExternalSeedersEndToEndTests(ProbeCase):
    """F4 的端到端最后一段：池里的 `external_seeders` 必须一路走到打分。

    单测绿、端到端断已经出现过两次（#1 写了字段没人读、#2 打分侧读了但 resource
    没带出来），所以这里钉的是**真实 probe 路径**：池条目 → probe 组 resource →
    rank_candidates → 排名行里的 `dimensions`/`breakdown`。
    """

    NO_ANNOUNCE_HASH = "3333333333333333333333333333333333333333"  # 夹具里的 Gamma

    def pool_without_announce(self, external_seeders=None, age_days=None):
        """把夹具第三个候选改成「announce 无任何实测」，可选带上外部快照种子数。"""
        payload = json.loads(FIXTURE_POOL.read_text(encoding="utf-8"))
        entry = payload["entries"][self.NO_ANNOUNCE_HASH]
        entry["metadata_at"] = self.now - 60
        entry["health_at"] = self.now - 60
        entry["seeds"] = 0
        entry["peers"] = 0
        entry.pop("external_peers", None)  # 没有 announce 结果 → 不是「死种」
        if external_seeders is not None:
            entry["external_seeders"] = external_seeders
        if age_days is not None:
            entry["external_seeders_age_days"] = age_days
        path = os.path.join(self.tmp, f"pool-ext-{external_seeders}-{age_days}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return path

    def ranked_speed(self, pool_path):
        """跑一次真实 probe 路径，取那个候选的 (speed 子分, breakdown.speed)。"""
        calls = []
        patchers = offline_network_guard(self)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        rc, out_path, _ = self.run_probe(pool_path, ["--no-live-check"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [], "离线用例里出现了网络调用")
        probe = self.read_json(out_path)
        rows = [row for row in probe["ranked"] if row["ih"] == self.NO_ANNOUNCE_HASH]
        self.assertTrue(rows, "第三个候选被剔掉了，用例前提不成立")
        return rows[0]["dimensions"]["speed"], rows[0]["breakdown"]["speed"]

    def test_missing_external_seeders_behaves_exactly_like_no_signal(self):
        """缺字段 = 与旧行为逐位相同：没有 announce、没有外部快照 → 中性 0.40。"""
        speed, parts = self.ranked_speed(self.pool_without_announce())
        self.assertEqual(parts["seed_source"], "none")
        self.assertEqual(parts["mode"], "unknown")
        self.assertAlmostEqual(speed, 40.0, delta=0.01)

    def test_external_seeders_reach_the_score_through_the_probe_path(self):
        """有外部快照（新鲜）→ speed 子分由外部先验决定，不再是恒 40.00/unknown。"""
        speed, parts = self.ranked_speed(
            self.pool_without_announce(external_seeders=5000, age_days=0.0)
        )
        self.assertEqual(parts["seed_source"], "external")
        self.assertEqual(parts["external_seeders"], 5000)
        self.assertEqual(parts["external_seeders_age_days"], 0.0)
        self.assertAlmostEqual(speed, 100.0, delta=0.01)

    def test_a_stale_external_snapshot_is_discounted_but_still_above_neutral(self):
        speed, parts = self.ranked_speed(
            self.pool_without_announce(external_seeders=5000, age_days=30.0)
        )
        self.assertEqual(parts["seed_source"], "external")
        self.assertAlmostEqual(parts["external_seeders_discount"], 0.54, delta=0.005)
        self.assertAlmostEqual(speed, 54.0, delta=0.01)
        self.assertGreater(speed, 40.0)

    def test_announce_measurement_still_wins_over_the_external_snapshot(self):
        """announce 实测优先：夹具前两个候选仍有 120 seeds，不该被外部值改写。"""
        payload = json.loads(FIXTURE_POOL.read_text(encoding="utf-8"))
        for entry in payload["entries"].values():
            entry["metadata_at"] = self.now - 60
            entry["health_at"] = self.now - 60
            entry["external_seeders"] = 5000
            entry["external_seeders_age_days"] = 0.0
        path = os.path.join(self.tmp, "pool-priority.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        speed, parts = self.ranked_speed(path)
        # Gamma 现在既有 announce 数据又有外部快照 → 先验必须来自 announce
        self.assertNotEqual(parts["seed_source"], "external")


class L1LadderBudgetBoundTests(unittest.TestCase):
    """N3 的 console 半边：L1 阶梯的最坏墙钟必须 ≤450s（另一半 t11/t15 的增量落盘不在此）。"""

    def setUp(self):
        self.source = (BACKEND / "console_server.py").read_text(encoding="utf-8")

    def _constants(self):
        re = __import__("re")
        namespace = {}
        for line in self.source.splitlines():
            match = re.match(
                r"(L1_MAX_QUERIES|L1_KEYWORD_BUDGET_SECS|SEARCH_POOL_TIMEOUT) = (\d+)", line
            )
            if match:
                namespace[match.group(1)] = int(match.group(2))
        return namespace

    def test_l1_ladder_worst_case_stays_within_the_bound(self):
        namespace = self._constants()
        for key in ("L1_MAX_QUERIES", "L1_KEYWORD_BUDGET_SECS", "SEARCH_POOL_TIMEOUT"):
            self.assertIn(key, namespace, key)
        worst = namespace["L1_MAX_QUERIES"] * namespace["L1_KEYWORD_BUDGET_SECS"]
        self.assertLessEqual(worst, 450, f"L1 最坏 {worst}s 超过 450s 上限（t10/N3）")
        self.assertLess(worst, namespace["SEARCH_POOL_TIMEOUT"], "L1 上界要小于它自己的阶段超时")

    def test_the_console_passes_the_budget_explicitly(self):
        """不能只靠 search_pool 的默认值：默认 45s × 12 = 540s 就贴上限了。"""
        self.assertIn('"--page-budget-secs", str(L1_KEYWORD_BUDGET_SECS)', self.source)
        self.assertIn('"--max-queries", str(L1_MAX_QUERIES)', self.source)

