"""回归守卫：正常路径排名对 golden、候选文件缺失时可读的退出码与提示。

拆自 `tests/backend_probe/test_backend_probe.py`（按主题），共享件在
`tests/backend_probe/_backend_probe_helpers.py`。"""

from __future__ import annotations

import json
import os

from tests.backend_probe._backend_probe_helpers import (
    FIXTURES,
    ProbeCase,
    offline_network_guard,
)

class RegressionTests(ProbeCase):
    def test_normal_path_ranking_matches_the_pre_change_golden(self):
        """同一份夹具输入，排名顺序、条数与改动前一致。

        golden_ranked.json 是改动前跑同一份夹具留下的。这里钉住**顺序与结构**，
        另外用「总分 = Σ(权重 × 维度)」做自洽校验：这样用例只对 probe_magnets
        的流水线负责，不会因为别人调整打分曲线（candidate_score）而变红。
        夹具名字里没有 cam 之类的来源惩罚、kind 都是 magnet，所以总分没有额外乘子。
        """
        golden = json.loads((FIXTURES / "golden_ranked.json").read_text(encoding="utf-8"))
        pool_path = self.pool_file()
        rc, out_path, _ = self.run_probe(pool_path, ["--no-live-check"])
        probe = self.read_json(out_path)

        self.assertEqual(rc, golden["exit_code"])
        self.assertEqual(
            [row["ih"] for row in probe["ranked"]],
            [row["ih"] for row in golden["ranked"]],
        )
        self.assertEqual(len(probe["ranked"]), len(golden["ranked"]))
        self.assertEqual(probe.get("rejected"), [])
        weights = probe["policy"]["weights"]
        for row in probe["ranked"]:
            expected = sum(row["dimensions"][name] * weights[name] for name in weights)
            self.assertAlmostEqual(
                row["score"], expected, delta=0.05,
                msg=f"{row['ih']} 的总分与各维度加权和不一致",
            )
        self.assertEqual((probe.get("best") or {}).get("ih"), golden["best_ih"])


class MissingCandidateFileTests(ProbeCase):
    """候选文件读不到、但给了云盘候选时不该整轮退出（复核报告 F1）。

    控制台只有在**本轮真的写出过**候选文件时才传 `--candidate-file`（见
    `CloudChannelWiringTests`），但直接调用/旧脚本仍可能指到一个不存在的路径。
    那时 `--extra-candidates` 里的云盘候选是独立的一条通道，不能被一起丢掉：
    rc=2 会让面板什么都看不到，而且 `--extra-candidates` 连读都没读到。
    """

    def write_extras(self):
        path = os.path.join(self.tmp, "pan.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {
                        "id": "pan-1",
                        "kind": "pan",
                        "name": "Cloud.Only.2024.1080p.WEB-DL.x265.CHS.mkv",
                        "share_url": "https://pan.xunlei.com/s/abcdefghijklmnopqrst?pwd=abcd",
                        "main_video_gb": 2.0,
                        "size_gb": 2.1,
                        "videos": [
                            {"index": 0, "path": "c.mkv", "name": "c.mkv",
                             "size": 2 * 1024 ** 3}
                        ],
                    }
                ],
                handle,
                ensure_ascii=False,
            )
        return path

    def test_cloud_candidates_survive_a_missing_candidate_file(self):
        extras = self.write_extras()
        missing = os.path.join(self.tmp, "does-not-exist.magnets")
        for patcher in offline_network_guard(self):
            patcher.start()
            self.addCleanup(patcher.stop)

        rc, out_path, stdout = self.run_probe(
            self.pool_file(),
            extra_args=(
                # 同名参数后者胜出：这里把 run_probe 的默认候选文件覆盖成不存在的路径
                "--candidate-file", missing,
                "--extra-candidates", extras,
                "--no-live-check",
            ),
        )
        self.assertNotEqual(rc, 2, f"候选文件读不到就把整轮退掉了：{stdout[-300:]}")
        self.assertIn("CANDIDATE_FILE_MISSING", stdout)
        self.assertIn("合并 1 个云盘/非磁力候选", stdout)
        with open(out_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertTrue(payload.get("ranked"), payload)
        self.assertIn("pan-1", [item.get("ih") for item in payload["ranked"]])

