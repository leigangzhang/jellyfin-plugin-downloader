"""交付前过滤：**磁力候选没有做种就丢**（seeds 缺失/None/非数字/<=0），云盘候选原样保留。

使用方口径变更（2026-10-15，真机佐证）：0 种子能用只是概率问题，好用且下载快的概率更低
—— 实测一条「种子 0/6」的候选确实过了元数据闸门并建了任务（迅雷 taskid 307），但速率只有
几 KB/s、预计要一天以上。所以判据从「可达性判决 `is_dead_swarm`」收紧为「有没有做种」，
过滤仍在 `backend/probe/probe_round.py` 组装交付候选这一步（不进快照数据）。

四类 fixture：seeds=0 / seeds 缺失 / seeds>0 / 云盘（没有 seeds 字段）。全部离线、无网络、无时钟。
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from tests.backend_probe._backend_integration_helpers import (  # noqa: F401 -- 导入即把 backend/ 加进 sys.path
    BACKEND,
    EXIT_CODES,
    FIXTURES,
    IntegrationCase,
    pm,
)

from probe import probe_round  # noqa: E402

SEEDED = {
    "ih": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "name": "有做种的磁力候选",
    "score": 70.0,
    "dimensions": {"quality": 80.0},
    "seeds": 5,
    "peers": 7,
    "kind": "magnet",
}
ZERO_SEED = {
    "ih": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    "name": "0 种子的磁力候选",
    "score": 90.0,
    "dimensions": {"quality": 90.0},
    "seeds": 0,
    "peers": 6,           # 有 peer 也照丢：使用方口径按「有没有做种」判，不看可达性
    "kind": "magnet",
}
MISSING_SEEDS = {
    "ih": "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
    "name": "seeds 字段缺失的磁力候选",
    "score": 85.0,
    "dimensions": {"quality": 85.0},
    "kind": "magnet",
}
PAN_CANDIDATE = {
    "ih": "DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
    "name": "云盘候选（没有做种这个概念）",
    "score": 80.0,
    "dimensions": {"quality": 80.0},
    "kind": "pan",
    "share_url": "https://pan.xunlei.com/s/x?pwd=abcd",
}
PAN_WITH_ZERO_SEEDS = dict(PAN_CANDIDATE, ih="E" * 40, seeds=0)


class HasNoSeederTest(unittest.TestCase):
    def test_zero_missing_garbage_and_negative_are_dropped(self):
        for item in ({"seeds": 0}, {"seeds": None}, {}, {"seeds": "0"}, {"seeds": "abc"}, {"seeds": -1}):
            self.assertTrue(probe_round.has_no_seeder(item), item)

    def test_positive_seeds_survive(self):
        for item in ({"seeds": 5}, {"seeds": "5"}, {"seeds": 0.5}):
            self.assertFalse(probe_round.has_no_seeder(item), item)

    def test_pan_is_never_dropped(self):
        for item in (PAN_CANDIDATE, PAN_WITH_ZERO_SEEDS, {"kind": "pan"}, {"kind": "pan", "seeds": None}):
            self.assertFalse(probe_round.has_no_seeder(item), item)


class DropNoSeederTest(unittest.TestCase):
    def test_split_keeps_only_seeded_and_pan(self):
        kept, dropped = probe_round.drop_no_seeder([SEEDED, ZERO_SEED, MISSING_SEEDS, PAN_CANDIDATE])
        self.assertEqual([item["ih"] for item in kept], [SEEDED["ih"], PAN_CANDIDATE["ih"]])
        self.assertEqual([item["ih"] for item in dropped], [ZERO_SEED["ih"], MISSING_SEEDS["ih"]])

    def test_empty_input(self):
        self.assertEqual(probe_round.drop_no_seeder([]), ([], []))
        self.assertEqual(probe_round.drop_no_seeder(None), ([], []))


class RankPhaseDeliveryTest(unittest.TestCase):
    """交付组装这一步：没做种的磁力候选不进 ranked，但留下可数的记录与条数。"""

    def _run(self, candidates):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "probe.json")
            args = SimpleNamespace(query="里斯本丸沉没", kind="record", live_check=False,
                                   min_score=45, json_out=out_path)
            budget = SimpleNamespace(timeout_stage=None, expired_stages=[], total_seconds=1.0)
            timers = SimpleNamespace(seconds={})
            buf = io.StringIO()
            # rank_candidates / format_candidate_line 都在 probe_round 的命名空间里，
            # 打桩掉打分与终端格式化，让用例只验「交付前过滤」这一个行为。
            with mock.patch.object(probe_round, "rank_candidates", lambda items, policy: list(items)), \
                    mock.patch.object(probe_round, "format_candidate_line", lambda item, index: ""):
                with redirect_stdout(buf):
                    try:
                        result = probe_round.rank_phase(
                            args, budget, timers, {}, time.time(), candidates, [], [], {},
                            {}, time.time(), out_path, [], None,
                        )
                        exit_code = None
                    except SystemExit as exc:
                        result = None
                        exit_code = exc.code
            payload = None
            if os.path.exists(out_path):
                with open(out_path, encoding="utf-8") as handle:
                    payload = json.load(handle)
            return result, exit_code, payload, buf.getvalue()

    def test_no_seeder_is_dropped_and_counted_pan_kept(self):
        result, exit_code, payload, stdout = self._run([SEEDED, ZERO_SEED, MISSING_SEEDS, PAN_CANDIDATE])
        ranked, output, best = result
        self.assertIsNone(exit_code)
        self.assertEqual([item["ih"] for item in ranked], [SEEDED["ih"], PAN_CANDIDATE["ih"]])
        self.assertEqual(output["no_seeder_filtered"], 2)
        self.assertEqual([item["ih"] for item in output["ranked"]], [SEEDED["ih"], PAN_CANDIDATE["ih"]])
        self.assertEqual(best["ih"], SEEDED["ih"])
        self.assertIn("NO_SEEDER_FILTERED 2", stdout)
        dropped = [item for item in output["rejected"] if item.get("reason") == "dead_swarm_filtered"]
        self.assertEqual(len(dropped), 2)
        self.assertIsNone(payload)  # rank_phase 只组装；落盘由 deliver() 负责

    def test_all_no_seeder_delivers_empty_round(self):
        result, exit_code, payload, stdout = self._run([ZERO_SEED, MISSING_SEEDS])
        ranked, output, best = result
        self.assertEqual(ranked, [])
        self.assertIsNone(best)
        self.assertIsNone(exit_code)          # 不再抛退出码：交给调用方按 best is None 收尾
        self.assertEqual(output["ranked"], [])
        self.assertIsNone(output["best"])
        self.assertEqual(output["no_seeder_filtered"], 2)
        self.assertIn("NO_CANDIDATES", stdout)
        self.assertEqual(payload["ranked"], [])   # 空轮也落盘

    def test_pan_only_round_is_untouched(self):
        result, exit_code, payload, stdout = self._run([PAN_CANDIDATE, PAN_WITH_ZERO_SEEDS])
        ranked, output, best = result
        self.assertIsNone(exit_code)
        self.assertEqual(output["no_seeder_filtered"], 0)
        self.assertEqual([item["ih"] for item in output["ranked"]],
                         [PAN_CANDIDATE["ih"], PAN_WITH_ZERO_SEEDS["ih"]])
        self.assertNotIn("NO_SEEDER_FILTERED", stdout)


class AllFilteredEndToEndTest(IntegrationCase):
    """整轮都被滤空时的真实行为：走 pm.main()，退出码 4 + NO_CANDIDATES + 空 ranked 落盘。"""

    def zero_seed_pool(self):
        """夹具 pool 的副本，把三个 entry 的 seeds/peers 都改成 0（0 种子那种）。"""
        payload = json.loads((FIXTURES / "pool.json").read_text(encoding="utf-8"))
        for entry in payload["entries"].values():
            entry["seeds"] = 0
            entry["peers"] = 0
            entry["external_peers"] = 0
            entry["metadata_at"] = self.now - 5
            entry["health_at"] = self.now - 5
        return self._write_json("pool-zero-seed.json", payload)

    def test_every_candidate_filtered_yields_no_candidates(self):
        rc, out_path, stdout = self.run_probe(self.zero_seed_pool(), ["--no-live-check"])
        self.assertEqual(rc, EXIT_CODES["no_candidate"])
        self.assertIn("NO_CANDIDATES", stdout)
        self.assertIn("NO_SEEDER_FILTERED 3", stdout)
        payload = self.read_json(out_path)
        self.assertEqual(payload["ranked"], [])
        self.assertIsNone(payload["best"])
        self.assertEqual(payload["no_seeder_filtered"], 3)


if __name__ == "__main__":
    unittest.main()
