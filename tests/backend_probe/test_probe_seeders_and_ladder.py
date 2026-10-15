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

    def filtered_row(self, pool_path):
        """跑一次真实 probe 路径，返回 (rc, probe JSON, 交付的 ih 列表, stdout)。

        口径变更（使用方 2026-10-15）：0 种子的磁力候选**不再交付**，所以这里不再断言
        「那个候选的 speed 子分是多少」，而是断言它根本没进 `ranked`。
        它的 speed 语义（`unknown` 中性 0.40、`seeds-external` 外部先验与折扣）改由
        `tests/backend_probe/test_backend_scoring.py`（29 例，含
        `test_external_seeders_reach_the_prior_when_announce_is_silent`）在打分层覆盖。
        """
        calls = []
        for patcher in offline_network_guard(self):
            patcher.start()
            self.addCleanup(patcher.stop)
        rc, out_path, stdout = self.run_probe(pool_path, ["--no-live-check"])
        self.assertEqual(calls, [], "离线用例里出现了网络调用")
        probe = self.read_json(out_path)
        return rc, probe, [row["ih"] for row in probe["ranked"]], stdout

    def assert_silent_candidate_is_filtered(self, pool_path):
        """「announce 无读数 + seeds=0」的候选必须被交付前过滤掉，且留下可数记录。

        旧口径把它当「unknown ≠ 不可达」保留（t48 的 is_dead_swarm 判据）；使用方纠正：
        0 种子能用只是概率问题，好用且下载快的概率更低 —— 实测一条「种子 0/6」的候选确实
        过了元数据闸门并建了任务（迅雷 taskid 307），但速率只有几 KB/s、预计要一天以上。
        所以现在按「有没有做种」过滤，`seeds=0`（含缺失）一律丢。
        """
        rc, probe, hashes, stdout = self.filtered_row(pool_path)
        self.assertEqual(rc, 0)
        self.assertNotIn(self.NO_ANNOUNCE_HASH, hashes)
        self.assertEqual(len(probe["ranked"]), 2, "剩下两条（seeds=120）应当照常交付")
        self.assertIn("NO_SEEDER_FILTERED 1", stdout)
        self.assertEqual(probe["no_seeder_filtered"], 1)
        dropped = [row for row in probe["rejected"] if row.get("reason") == "dead_swarm_filtered"]
        self.assertEqual([row["ih"] for row in dropped], [self.NO_ANNOUNCE_HASH])
        return probe

    def test_missing_external_seeders_candidate_is_filtered_out(self):
        """没有 announce、没有外部快照、seeds=0 → 交付前丢弃（旧口径是保留 + 中性 0.40）。"""
        self.assert_silent_candidate_is_filtered(self.pool_without_announce())

    def test_external_seeders_do_not_rescue_a_zero_seed_candidate(self):
        """有外部快照（新鲜）也不放行：做种数为 0 就丢（外部先验的算术另在打分层覆盖）。"""
        self.assert_silent_candidate_is_filtered(
            self.pool_without_announce(external_seeders=5000, age_days=0.0)
        )

    def test_stale_external_snapshot_does_not_rescue_a_zero_seed_candidate(self):
        """陈旧外部快照同理：0 种子一律丢，不再按折扣后的先验放行。"""
        self.assert_silent_candidate_is_filtered(
            self.pool_without_announce(external_seeders=5000, age_days=30.0)
        )

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
        # 这条用例不涉及 0 种子（三个 entry 都是 seeds=120），交付名单仍是 3 条；
        # 具体先验来自 announce 的断言在打分层（test_backend_scoring.py）覆盖。
        rc, probe, hashes, _ = self.filtered_row(path)
        self.assertEqual(rc, 0)
        self.assertIn(self.NO_ANNOUNCE_HASH, hashes)
        self.assertEqual(probe["no_seeder_filtered"], 0)


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

