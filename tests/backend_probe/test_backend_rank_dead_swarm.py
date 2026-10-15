"""排序主键：「注定过不了提交闸门」的候选沉到所有可提交候选之后。

Subject：`backend/candidate_score/score.py` 的 `rank_candidates`（以及它用的
`backend/candidate_score/speed.py` 的 `is_dead_swarm`）。

真机复现（2026-10-15，快照 `侏罗纪世界3.probe.json`）：用户取到 4 条候选，面板头部
两条是 0 种子，点第一条提交 → 26.3 秒后 `rc=3 RESOURCE_REJECTED`（`aria2c
--bt-stop-timeout=25` 拿不到 metadata）。快照里的实际数字：

* `A01D509E9F4F17…`  记录分 **69.62**，`breakdown.speed.mode = "dead-swarm"`
  （announce 报 `external_peers == 0`），seeds 0 / peers 0；
* `338F819A7E01BB…`  记录分 **67.76**，`mode = "seeds"`，seeds 0 / peers 6。

改前 `rank_candidates` 只按 `(score, seeds, -|gb|)` 倒序，于是 dead 的排在前。下面
的用例把这两条候选的**真实字段**照抄进来（去掉 `score`/`dimensions`/`breakdown`
这些派生键，`rank_candidates` 会重新算分；重算得到 68.89 / 67.46 —— 绝对值与快照
略有差异，因为快照那轮的目标体积/时长口径不同，但「dead 那条分更高」的关系一致，
所以复现面成立）。

全部离线、无网络、无时钟。
"""

from __future__ import annotations

import unittest

from tests.backend_probe._backend_probe_helpers import (  # noqa: F401 -- 导入即把 backend/ 加进 sys.path
    BACKEND,
)

from candidate_score.score import rank_candidates  # noqa: E402
from candidate_score.speed import is_dead_swarm  # noqa: E402

# 快照 `侏罗纪世界3.probe.json` 里两条候选的真实输入字段（派生键已去掉）。
DEAD_CANDIDATE = {
    "ih": "A01D509E9F4F17FFCCC9CF984B56680758CFB445",
    "name": "【更多高清电影访问 www.BBSDDS.com】侏罗纪世界3[简繁英特效字幕]."
            "Jurassic.World.Dominion.2022.1080p.H264.MA.WEB-DL.Atmos.DDP5.1-CHDBits",
    "seeds": 0, "peers": 0, "main_video_gb": 8.997523876838386,
    "estimated_bitrate_mbps": 10.73446411,
    "resolution": "1080p", "source": "webdl", "codec": "h264",
    "has_chs": True, "hard_sub": False, "release_group": "chd", "kind": "magnet",
    "dead_swarm": True,
}

LIVE_CANDIDATE = {
    "ih": "338F819A7E01BB093A93368D4E1999C25EECAC16",
    "name": "【首发于高清影视之家 www.BBQDDQ.com】侏罗纪世界3[中文字幕+国语音轨]."
            "Jurassic.World.Dominion.2022.1080p.WEB-DL.H264.DDP5.1-TAGWEB",
    "seeds": 0, "peers": 6, "main_video_gb": 5.300659201107919,
    "estimated_bitrate_mbps": 6.323932754444445,
    "resolution": "1080p", "source": "webdl", "codec": "h264",
    "has_chs": True, "hard_sub": False, "release_group": "", "kind": "magnet",
    "dead_swarm": False,
}


def _copy(candidate):
    return {key: value for key, value in candidate.items()}


class DeadSwarmSinksTests(unittest.TestCase):
    def test_the_real_dead_swarm_pair_is_reordered(self):
        """真机那一对：改前 dead 在前，改后必须沉到可提交候选之后。"""
        dead, live = _copy(DEAD_CANDIDATE), _copy(LIVE_CANDIDATE)
        ranked = rank_candidates([dead, live])
        order = [item["ih"][:12] for item in ranked]
        self.assertLess(
            order.index("338F819A7E01"), order.index("A01D509E9F4F"),
            "dead-swarm 候选必须排在非 dead 候选之后（真机复现：改前它在前）",
        )
        by_ih = {item["ih"][:12]: item["score"] for item in ranked}
        self.assertGreater(
            by_ih["A01D509E9F4F"], by_ih["338F819A7E01"],
            "前提确认：dead 那条重算后的分数仍然更高，否则这条守卫是空的")        # 反过来的顺序也要成立：输入顺序不该影响判决
        ranked_reversed = rank_candidates([_copy(LIVE_CANDIDATE), _copy(DEAD_CANDIDATE)])
        self.assertTrue(ranked_reversed[0]["ih"].startswith("338F819A7E01"))

    def test_the_verdict_is_readable_on_the_candidate(self):
        """前端要能直接读，不必自己去翻 breakdown。"""
        ranked = rank_candidates([_copy(DEAD_CANDIDATE), _copy(LIVE_CANDIDATE)])
        by_ih = {item["ih"][:12]: item for item in ranked}
        self.assertIs(by_ih["A01D509E9F4F"]["dead_swarm"], True)
        self.assertIs(by_ih["A01D509E9F4F"]["submittable"], False)
        self.assertIs(by_ih["338F819A7E01"]["dead_swarm"], False)
        self.assertIs(by_ih["338F819A7E01"]["submittable"], True)

    def test_a_precomputed_verdict_counts_even_without_the_flag(self):
        """判据早就存在 `breakdown.speed.mode` 里：只剩它的候选同样要被认出来。"""
        precomputed = _copy(DEAD_CANDIDATE)
        precomputed.pop("dead_swarm")
        precomputed["breakdown"] = {"speed": {"mode": "dead-swarm", "score_source": "dead-swarm"}}
        self.assertTrue(is_dead_swarm(precomputed))
        ranked = rank_candidates([precomputed, _copy(LIVE_CANDIDATE)])
        self.assertTrue(ranked[0]["ih"].startswith("338F819A7E01"))
        self.assertIs(ranked[1]["dead_swarm"], True)

    def test_unknown_mode_is_not_dead(self):
        """`unknown`（tracker 抓不到）不是死种判决，不能拿它当闸门。"""
        unknown = _copy(LIVE_CANDIDATE)
        unknown["breakdown"] = {"speed": {"mode": "unknown", "score_source": "neutral"}}
        self.assertFalse(is_dead_swarm(unknown))
        self.assertFalse(is_dead_swarm({}))


class OrderPreservedTests(unittest.TestCase):
    """闸门只分两类；类内必须保持原来的排序。"""

    @staticmethod
    def _variant(ih, score_hint_gb, dead):
        item = _copy(LIVE_CANDIDATE)
        item["ih"] = ih
        item["name"] = f"样本 {ih}"
        item["main_video_gb"] = score_hint_gb
        item["dead_swarm"] = dead
        return item

    def test_all_dead_keeps_the_score_order(self):
        items = [self._variant("A" * 40, 5.0, True),
                 self._variant("B" * 40, 9.0, True),
                 self._variant("C" * 40, 7.0, True)]
        ranked = rank_candidates(items)
        self.assertEqual([item["score"] for item in ranked],
                         sorted((item["score"] for item in ranked), reverse=True),
                         "全 dead 时顺序必须与改前一致：仍然按 score 倒序")
        self.assertEqual([item["rank"] for item in ranked], [1, 2, 3])

    def test_live_candidates_keep_their_relative_order(self):
        items = [self._variant("D" * 40, 5.0, False),
                 self._variant("E" * 40, 9.0, False),
                 self._variant("F" * 40, 7.0, False)]
        ranked = rank_candidates(items)
        self.assertEqual([item["score"] for item in ranked],
                         sorted((item["score"] for item in ranked), reverse=True))
        self.assertTrue(all(item["submittable"] for item in ranked))

    def test_dead_candidates_keep_their_relative_order_behind_the_live_ones(self):
        dead_a = self._variant("A" * 40, 5.0, True)
        dead_b = self._variant("B" * 40, 9.0, True)
        ranked = rank_candidates([dead_a, dead_b, self._variant("D" * 40, 5.0, False)])
        self.assertTrue(ranked[0]["ih"].startswith("D"), "非 dead 必须排在前面")
        dead_scores = [item["score"] for item in ranked[1:]]
        self.assertEqual(dead_scores, sorted(dead_scores, reverse=True),
                         "dead 之间仍按原来的 score 倒序（不是按输入顺序）")
        self.assertEqual([item["rank"] for item in ranked], [1, 2, 3])

    def test_rescoring_keeps_an_already_computed_verdict(self):
        """重算分不能把「只剩 breakdown 的判决」洗掉——快照那条路径就靠它。"""
        item = self._variant("G" * 40, 5.0, True)
        item.pop("dead_swarm")
        item["breakdown"] = {"speed": {"mode": "dead-swarm", "score_source": "dead-swarm"}}
        rank_candidates([item, self._variant("D" * 40, 5.0, False)])
        self.assertIs(item["dead_swarm"], True)
        self.assertIs(item["submittable"], False)

    def test_explicit_false_survives_a_stale_breakdown(self):
        """live-check 把候选救回后（显式 False），上一轮的旧明细不得再把它判死。"""
        rescued = self._variant("H" * 40, 5.0, False)
        rescued["breakdown"] = {"speed": {"mode": "dead-swarm", "score_source": "dead-swarm"}}
        rank_candidates([rescued])
        self.assertIs(rescued["dead_swarm"], False)
        self.assertIs(rescued["submittable"], True)


if __name__ == "__main__":
    unittest.main()
