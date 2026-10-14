"""PROBE_PARTIAL 的消费者与「本轮没产出时不拿上一轮快照冒充」。

拆自 `tests/backend_probe/test_backend_probe.py`（按主题），共享件在
`tests/backend_probe/_backend_probe_helpers.py`。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.backend_probe._backend_probe_helpers import (
    console_module,
)

class ProbePartialStatusTests(unittest.TestCase):
    """N1：`PROBE_PARTIAL` 必须有消费者，partial/timeout_stage 进得了面板快照。"""

    def setUp(self):
        self.cs = console_module()

    def test_partial_round_without_a_deliverable_reports_probe_partial(self):
        got = self.cs._search_status(
            'PROBE_PARTIAL {"timeout_stage": "metadata"}\nNO_ELIGIBLE_RESOURCE', {}, 4
        )
        self.assertEqual(got, "probe_partial")

    def test_deliverable_round_keeps_its_own_status(self):
        probe = {"ranked": [{"ih": "A" * 40}], "best": {"ih": "A" * 40}}
        self.assertEqual(self.cs._search_status("PROBE_PARTIAL {}", probe, 0), "ok")

    def test_specific_failure_tokens_still_win_over_partial(self):
        self.assertEqual(self.cs._search_status("PROBE_PARTIAL {}\nSWARM_DEAD {}", {}, 8), "swarm_dead")
        self.assertEqual(self.cs._search_status("PROBE_PARTIAL {}\nMETADATA_FAILED {}", {}, 7), "metadata_failed")
        self.assertEqual(self.cs._search_status("NO_ELIGIBLE_RESOURCE", {}, 4), "no_eligible_resource")

    def test_probe_partial_line_is_parsed(self):
        self.assertEqual(
            self.cs._probe_partial('x\nPROBE_PARTIAL {"timeout_stage": "health"}\ny'),
            (True, "health"),
        )
        self.assertEqual(self.cs._probe_partial("PROBE_PARTIAL"), (True, ""))
        self.assertEqual(self.cs._probe_partial("nothing"), (False, ""))

    def test_snapshot_carries_the_partial_flags(self):
        import build_console as bc

        with mock.patch.object(bc, "list_thunder_tasks", lambda: []), \
             mock.patch.object(bc, "list_watches", lambda: []):
            snap = bc.build_snapshot(
                "no-such-slug", probe={}, ctx={},
                probe_partial=True, probe_timeout_stage="health", probe_round_empty=True,
            )
        self.assertTrue(snap["probe_partial"])
        self.assertEqual(snap["probe_timeout_stage"], "health")
        self.assertTrue(snap["probe_round_empty"])

    def test_snapshot_exposes_partial_written_by_probe_itself(self):
        import build_console as bc

        probe = {"query": "x", "ranked": [], "partial": True, "timeout_stage": "metadata"}
        with mock.patch.object(bc, "list_thunder_tasks", lambda: []), \
             mock.patch.object(bc, "list_watches", lambda: []):
            snap = bc.build_snapshot("no-such-slug", probe=probe, ctx={})
        self.assertTrue(snap["probe_partial"])
        self.assertEqual(snap["probe_timeout_stage"], "metadata")
        self.assertFalse(snap["probe_round_empty"])


class ConsoleRoundsWithoutOutputTests(unittest.TestCase):
    """N2：本轮没写出 probe JSON 时，不许把上一轮快照当成本轮结果回写。

    复现方式与 t10 相同：把 `bc.SNAPSHOTS_DIR`/`MARKS_DIR` 指到临时目录，桩掉
    `run_cli`/`run_cli_stream`，让「probe」既不写 JSON 又把 PROBE_PARTIAL 打到
    stdout；断言磁盘上那份旧快照的**内容和 mtime 都没变**，而本轮 job 的快照是
    「空 + partial」而不是旧候选。
    """

    PREVIOUS = {
        "query": "previous",
        "kind": "movie",
        "ranked": [
            {
                "rank": 1, "ih": "F" * 40, "name": "Previous.Good.Snapshot",
                "score": 88.0, "seeds": 10, "breakdown": {},
                "dimensions": {"quality": 90.0, "speed": 90.0, "experience": 80.0, "fit": 80.0},
            }
        ],
        "best": {"ih": "F" * 40, "name": "Previous.Good.Snapshot", "score": 88.0},
        "rejected": [], "policy": {}, "min_score": 45,
    }

    PARTIAL_LINE = (
        'PROBE_PARTIAL {"timeout_stage": "metadata", "expired_stages": ["metadata"], '
        '"budget_secs": 720, "completed": {"health": 3, "metadata": 0}}'
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jmd-n2-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cs = console_module()
        import build_console as bc

        self.bc = bc
        self.snapshots = os.path.join(self.tmp, "snapshots")
        self.marks = os.path.join(self.tmp, "marks")
        os.makedirs(self.snapshots)
        os.makedirs(self.marks)
        self.probe_path = os.path.join(self.snapshots, "demo.probe.json")
        with open(self.probe_path, "w", encoding="utf-8") as handle:
            json.dump(self.PREVIOUS, handle, ensure_ascii=False)
        with open(os.path.join(self.snapshots, "demo.context.json"), "w", encoding="utf-8") as handle:
            json.dump({"title": "Demo", "kind": "movie", "search_status": "ok"}, handle)
        # 把「上一轮」快照的 mtime 明确放到过去，而不是 sleep 等时间流逝：
        # 固定等待在本仓是被 no-time-based-test-sync 明令禁止的测试写法
        # （tools/workflow.json），而 os.utime 既确定又不受时间戳粒度影响。
        past = time.time() - 120
        os.utime(self.probe_path, (past, past))

        self.params = {
            "title": "Demo", "kind": "movie", "category": "Movies", "slug": "demo",
            "key": "demo", "duration": 120.0, "year": "", "alt_title": [],
            "season": None, "episode": None, "allow_pansou": False, "allow_pan": False,
            "sources": "magnet", "original_title": "", "strict_season": True,
            "final_dir": self.tmp, "probe_kind": "movie",
            "pool_file": os.path.join(self.tmp, "pool.json"),
            "cand_file": os.path.join(self.tmp, "cands.magnets"),
            "probe_out": self.probe_path,
        }

    def _run_round(self, stream_result):
        patchers = [
            mock.patch.object(self.bc, "SNAPSHOTS_DIR", self.snapshots),
            mock.patch.object(self.bc, "MARKS_DIR", self.marks),
            mock.patch.object(self.bc, "list_thunder_tasks", lambda: []),
            mock.patch.object(self.bc, "list_watches", lambda: []),
            mock.patch.object(self.cs, "run_cli", lambda *a, **k: (0, "", "")),
            mock.patch.object(self.cs, "run_cli_stream", stream_result),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        jid = self.cs._new_job()
        self.cs._run_search_job(jid, self.params)
        job = self.cs.JOBS[jid]
        self.assertEqual(job.get("error"), "", job.get("error"))
        return job

    def test_a_round_that_wrote_nothing_keeps_the_previous_snapshot_untouched(self):
        before_bytes = Path(self.probe_path).read_bytes()
        before_mtime = os.path.getmtime(self.probe_path)

        job = self._run_round(lambda *a, **k: (4, [self.PARTIAL_LINE, "NO_ELIGIBLE_RESOURCE"]))

        self.assertEqual(
            Path(self.probe_path).read_bytes(), before_bytes,
            "本轮没有产出，却把上一轮的快照重写了（t10/N2）",
        )
        self.assertEqual(
            os.path.getmtime(self.probe_path), before_mtime,
            "本轮没有产出，却刷新了上一轮快照的 mtime → 下游无法识别陈旧",
        )
        self.assertEqual(job["search_status"], "probe_partial")
        snapshot = job["snapshot"]
        self.assertEqual(snapshot["candidates"], [], "旧候选被当成本轮结果展示")
        self.assertTrue(snapshot["probe_partial"])
        self.assertEqual(snapshot["probe_timeout_stage"], "metadata")
        self.assertTrue(snapshot["probe_round_empty"])
        self.assertEqual(self.bc.read_context("demo")["search_status"], "probe_partial")

    def test_a_round_that_did_write_still_saves_its_result(self):
        fresh = {
            "query": "demo", "kind": "movie",
            "ranked": [{"rank": 1, "ih": "A" * 40, "name": "Fresh.Result", "score": 91.0,
                        "seeds": 12, "breakdown": {},
                        "dimensions": {"quality": 90.0, "speed": 90.0, "experience": 80.0, "fit": 80.0}}],
            "best": {"ih": "A" * 40, "name": "Fresh.Result", "score": 91.0},
            "rejected": [], "policy": {}, "min_score": 45,
        }

        def stream(argv, on_line=None, timeout=600):
            with open(self.probe_path, "w", encoding="utf-8") as handle:
                json.dump(fresh, handle, ensure_ascii=False)
            return 0, ["BEST_RESULT_JSON " + self.probe_path]

        job = self._run_round(stream)

        self.assertEqual(job["search_status"], "ok")
        self.assertEqual([c["name"] for c in job["snapshot"]["candidates"]], ["Fresh.Result"])
        self.assertFalse(job["snapshot"]["probe_round_empty"])
        self.assertEqual(
            json.loads(Path(self.probe_path).read_text(encoding="utf-8"))["ranked"][0]["name"],
            "Fresh.Result",
        )

