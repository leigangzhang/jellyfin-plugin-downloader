"""0 种子候选（dead-swarm）「交付前丢弃」：probe JSON / 面板快照里不再有它们。

使用方口径（2026-10-15 真机复核）：这类候选「永远用不到」，不保存在数据里 —— 过滤放在
`backend/probe/probe_round.py` 组装交付候选这一步，面板只报一句「已过滤 N 条 0 种子候选」。

判定用的是**产品自己的可达性判决** `candidate_score.speed.is_dead_swarm`：
seeds/peers 只是输入之一，真正的判决是「announce/live-check 没看到任何非本机 peer」。

三个 fixture：可提交（有外部 peer，正常保留）/ dead-swarm（丢弃）/ `mode == "unknown"`
（tracker 没读数 —— 按 speed.py 的注释是「不知道」不是「不可达」，**必须保留**）；
另有云盘（pan）候选完全没有 seeds 字段，也必须保留。全部离线、无网络、无时钟。
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

from tests.backend_probe._backend_probe_helpers import (  # noqa: F401 -- 导入即把 backend/ 加进 sys.path
    BACKEND,
)

from probe import probe_round  # noqa: E402

SEEDED = {
    "ih": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "name": "可提交的候选（有外部 peer）",
    "score": 70.0,
    "dimensions": {"quality": 80.0},
    "seeds": 5,
    "peers": 7,
    "kind": "magnet",
    "dead_swarm": False,
}
DEAD_SWARM = {
    "ih": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    "name": "0 种子候选（announce 没有非本机 peer）",
    "score": 90.0,
    "dimensions": {"quality": 90.0},
    "seeds": 0,
    "peers": 0,
    "kind": "magnet",
    "dead_swarm": True,
}
UNKNOWN_SIGNAL = {
    "ih": "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
    "name": "tracker 没有读数（unknown，不是死种）",
    "score": 85.0,
    "dimensions": {"quality": 85.0},
    "seeds": 0,
    "peers": 0,
    "kind": "magnet",
    "breakdown": {"speed": {"mode": "unknown"}},
}
DEAD_BY_MODE = {
    "ih": "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
    "name": "死种（由 breakdown 判出）",
    "score": 88.0,
    "dimensions": {"quality": 88.0},
    "seeds": 0,
    "peers": 0,
    "kind": "magnet",
    "breakdown": {"speed": {"mode": "dead-swarm"}},
}
PAN_CANDIDATE = {
    "ih": "DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
    "name": "云盘候选（没有 seeds 字段）",
    "score": 80.0,
    "dimensions": {"quality": 80.0},
    "kind": "pan",
    "share_url": "https://pan.xunlei.com/s/x?pwd=abcd",
}


class IsDeadCandidateTest(unittest.TestCase):
    def test_dead_swarm_bit_and_mode_are_dropped(self):
        self.assertTrue(probe_round.is_dead_candidate(DEAD_SWARM))
        self.assertTrue(probe_round.is_dead_candidate({"breakdown": {"speed": {"mode": "dead-swarm"}}}))

    def test_unknown_signal_and_cloud_survive(self):
        # 「不知道」不是「不可达」：unknown / 没有 seeds 字段的云盘候选都必须保留。
        self.assertFalse(probe_round.is_dead_candidate(UNKNOWN_SIGNAL))
        self.assertFalse(probe_round.is_dead_candidate(PAN_CANDIDATE))
        self.assertFalse(probe_round.is_dead_candidate(SEEDED))


class DropDeadCandidateTest(unittest.TestCase):
    def test_split_keeps_only_reachable(self):
        kept, dropped = probe_round.drop_dead_candidates([SEEDED, DEAD_SWARM, UNKNOWN_SIGNAL, PAN_CANDIDATE])
        self.assertEqual([item["ih"] for item in kept], [SEEDED["ih"], UNKNOWN_SIGNAL["ih"], PAN_CANDIDATE["ih"]])
        self.assertEqual([item["ih"] for item in dropped], [DEAD_SWARM["ih"]])

    def test_empty_input(self):
        self.assertEqual(probe_round.drop_dead_candidates([]), ([], []))
        self.assertEqual(probe_round.drop_dead_candidates(None), ([], []))


class RankPhaseDeliveryTest(unittest.TestCase):
    """交付组装这一步：丢掉的候选不进 ranked，但留下可数的记录与条数。"""

    def _run(self, candidates):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "probe.json")
            args = SimpleNamespace(query="里斯本丸沉没", kind="record", live_check=False,
                                   min_score=45, json_out=out_path)
            budget = SimpleNamespace(timeout_stage=None, expired_stages=[], total_seconds=1.0)
            timers = SimpleNamespace(seconds={})
            buf = io.StringIO()
            # rank_candidates / format_candidate_line 都在 probe_round 的命名空间里，
            # 打桩掉打分与终端格式化，让用例只验「交付前丢弃」这一个行为。
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

    def test_dead_swarm_is_dropped_and_counted(self):
        result, exit_code, payload, stdout = self._run([SEEDED, DEAD_SWARM, UNKNOWN_SIGNAL])
        ranked, output, best = result
        self.assertIsNone(exit_code)
        self.assertEqual([item["ih"] for item in ranked], [SEEDED["ih"], UNKNOWN_SIGNAL["ih"]])
        self.assertEqual(output["dead_swarm_filtered"], 1)
        self.assertEqual([item["ih"] for item in output["ranked"]], [SEEDED["ih"], UNKNOWN_SIGNAL["ih"]])
        self.assertEqual(best["ih"], SEEDED["ih"])
        self.assertIn("DEAD_SWARM_FILTERED 1", stdout)
        dropped = [item for item in output["rejected"] if item.get("reason") == "dead_swarm_filtered"]
        self.assertEqual(len(dropped), 1)
        self.assertIsNone(payload)  # rank_phase 只组装；落盘由 deliver() 负责

    def test_all_dead_swarm_keeps_the_existing_exit_contract(self):
        """整轮都是死种时**不丢**：保住 deliver() 的 BEST_DEAD_SWARM（rc=8）契约。

        deliver / 退出码矩阵依赖 `ranked[0]`，而 probe_magnets.main 只在 rc 非空时提前
        返回 —— 从 rank_phase 抛退出码会绕过那条契约。所以「整轮死种」这一支保持原样，
        混合轮里死种才丢（见上一个用例）。
        """
        result, exit_code, payload, stdout = self._run([DEAD_SWARM, DEAD_BY_MODE])
        output = result[1]
        self.assertIsNone(exit_code)
        self.assertEqual(output["dead_swarm_filtered"], 0)
        self.assertEqual([item["ih"] for item in output["ranked"]],
                         [DEAD_SWARM["ih"], DEAD_BY_MODE["ih"]])
        self.assertEqual(output["rejected"], [])
        self.assertNotIn("DEAD_SWARM_FILTERED", stdout)

    def test_seeded_only_round_unchanged(self):
        result, exit_code, payload, stdout = self._run([SEEDED])
        output = result[1]
        self.assertIsNone(exit_code)
        self.assertEqual(output["dead_swarm_filtered"], 0)
        self.assertEqual([item["ih"] for item in output["ranked"]], [SEEDED["ih"]])
        self.assertNotIn("DEAD_SWARM_FILTERED", stdout)


if __name__ == "__main__":
    unittest.main()
