"""云盘通道的接线：`console_server` 搜索任务里各站网页地址怎么变成 CLI 参数。

Subject: `backend/console_server.py` 的 `_run_search_job` 调用链（真跑一遍，
只把 CLI 调用换成记录器），以及每轮候选文件的命名与新鲜度判定。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.backend_retired._backend_retired_helpers import source_config


class CloudChannelWiringTests(unittest.TestCase):
    """驱动真实的 `console_server._run_search_job`，只把 CLI 调用换成记录器。"""

    def setUp(self):
        import build_console as bc  # noqa: PLC0415 - 导入控制台栈较重，放在用例里
        import console_server as cs  # noqa: PLC0415

        self.bc, self.cs = bc, cs
        self.tmp = tempfile.mkdtemp(prefix="jmd-cloud-wiring-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_round(self, sites, write_candidates=False):
        """跑一轮搜索任务，返回 (所有 CLI argv, job)。

        `write_candidates=True` 时记录器会在 search_pool 那一步真的写出候选文件，
        用来区分「本轮写的」与「上一轮留在同一个路径上的」。
        """
        calls = []
        snapshots = os.path.join(self.tmp, "snapshots")
        marks = os.path.join(self.tmp, "marks")
        os.makedirs(snapshots, exist_ok=True)
        os.makedirs(marks, exist_ok=True)
        params = {
            "title": "漫长的季节", "kind": "episode", "category": "Shows",
            "slug": "demo", "key": "demo", "duration": 50.0, "year": "",
            "alt_title": [], "season": 1, "episode": None, "sources": "both",
            "original_title": "", "strict_season": True, "publisher_only": True,
            "final_dir": self.tmp, "probe_kind": "episode",
            "pool_file": os.path.join(self.tmp, "pool.json"),
            "cand_file": os.path.join(self.tmp, "cands.magnets"),
            "probe_out": os.path.join(snapshots, "demo.probe.json"),
        }

        def record_cli(argv, timeout=600):
            calls.append(list(argv))
            return 0, "", ""

        def record_stream(argv, on_line=None, timeout=600):
            calls.append(list(argv))
            if write_candidates and argv and argv[0] == "search/search_pool.py":
                Path(os.path.join(self.tmp, "cands.magnets")).write_text(
                    "magnet:?xt=urn:btih:" + "A" * 40 + "\n", encoding="utf-8")
            return 0, []

        patches = [
            mock.patch.object(self.bc, "SNAPSHOTS_DIR", snapshots),
            mock.patch.object(self.bc, "MARKS_DIR", marks),
            mock.patch.object(self.bc, "list_thunder_tasks", lambda: []),
            mock.patch.object(self.bc, "list_watches", lambda: []),
            mock.patch.object(self.cs, "run_cli", record_cli),
            mock.patch.object(self.cs, "run_cli_stream", record_stream),
            mock.patch.dict(os.environ,
                            {source_config.ENV_SITES: json.dumps(sites,
                                                                 ensure_ascii=False)},
                            clear=False),
        ]
        for patcher in patches:
            patcher.start()
        try:
            jid = self.cs._new_job()
            self.cs._run_search_job(jid, params)
            job = dict(self.cs.JOBS[jid])
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        return calls, job

    def pan_calls(self, calls):
        return [argv for argv in calls if argv and argv[0] == "pan/pan_search.py"]

    def probe_argv(self, calls):
        return next((argv for argv in calls
                     if argv and argv[0] == "probe/probe_magnets.py"), [])

    def test_every_station_with_a_web_page_contributes_one_page_argument(self):
        sites = [
            dict(source_config.DEFAULT_SITES[0],
                 web_search_url="https://d.example/s?q={q}"),
            # 云盘专用站：只有网页搜索地址，没有接口地址
            {"name": "搜索页", "kind": "", "base_url": "",
             "web_search_url": "https://s.example/search?q={q}"},
        ]
        calls, job = self.run_round(sites)
        self.assertEqual(job.get("error"), "", job.get("error"))
        pan = self.pan_calls(calls)
        self.assertEqual(len(pan), 1, f"应当恰好调用一次 pan_search：{pan}")
        pages = [pan[0][i + 1] for i, arg in enumerate(pan[0]) if arg == "--page"]
        self.assertEqual(len(pages), 2, f"两个站各该带一个 --page：{pan[0]}")
        self.assertIn("d.example", pages[0])
        self.assertIn("s.example", pages[1])
        for url in pages:
            # 片名必须编码进 {q}，不能原样塞中文
            self.assertNotIn("{q}", url)
            self.assertNotIn("漫长的季节", url)

    def test_each_round_gets_its_own_candidate_file(self):
        """候选文件按轮命名：同名标题连开两轮不会互相读到对方的候选。

        复核第二轮 F3：固定 `/tmp/<slug>.magnets` 时只能靠 mtime 判断「是不是本轮写的」，
        而两个写者并发（同一季重搜、或不同季但同一个 slug）时那条判定就失效了。
        """
        captured = []
        bodies = [
            {"title": "漫长的季节", "kind": "episode", "sources": "magnet",
             "final_dir": self.tmp},
            {"title": "漫长的季节", "kind": "episode", "sources": "magnet",
             "final_dir": self.tmp},
        ]
        fake_self = mock.Mock()
        with mock.patch.object(self.cs, "_run_search_job",
                               lambda jid, params: captured.append(params)), \
                mock.patch.object(self.cs, "valid_target_dir", lambda _path: True), \
                mock.patch.object(self.cs.Handler, "_ok", lambda _self, obj: obj):
            for body in bodies:
                self.cs.Handler._api_search(fake_self, dict(body))

        self.assertEqual(len(captured), 2)
        paths = [params["cand_file"] for params in captured]
        self.assertNotEqual(paths[0], paths[1],
                            "两轮用了同一个候选文件路径，并发时会互相串候选")
        for path in paths:
            self.assertIn("漫长的季节", path)
            self.assertTrue(path.endswith(".magnets"), path)

    def test_a_stale_candidate_file_is_not_fed_to_the_probe(self):
        """候选文件路径固定、内容分轮次：本轮没写就不能喂给 probe。

        只配云盘专用站（或配置坏掉）时 search_pool 会早退、不写候选文件，而
        /tmp/<slug>.magnets 还留着上一轮的内容——照旧传 `--candidate-file` 会把上一轮的
        磁力当成本轮结果（复核报告 F1/F2）。
        """
        stale = Path(self.tmp) / "cands.magnets"
        stale.write_text("magnet:?xt=urn:btih:" + "B" * 40 + "\n", encoding="utf-8")
        past = time.time() - 120
        os.utime(stale, (past, past))

        calls, job = self.run_round([dict(source_config.DEFAULT_SITES[0])])
        self.assertEqual(job.get("error"), "", job.get("error"))
        argv = self.probe_argv(calls)
        self.assertTrue(argv, "probe 应该被调用")
        self.assertNotIn("--candidate-file", argv,
                         "上一轮的候选文件不该被当成本轮结果喂给 probe")
        lines = job.get("lines") or []
        self.assertTrue(any("不拿上一轮的文件喂 probe" in line for line in lines), lines)

    def test_a_candidate_file_written_this_round_is_fed_to_the_probe(self):
        calls, job = self.run_round([dict(source_config.DEFAULT_SITES[0])],
                                    write_candidates=True)
        self.assertEqual(job.get("error"), "", job.get("error"))
        argv = self.probe_argv(calls)
        self.assertIn("--candidate-file", argv)

    def test_no_configured_web_page_means_no_pan_search_call(self):
        calls, job = self.run_round([dict(source_config.DEFAULT_SITES[0])])
        self.assertEqual(job.get("error"), "", job.get("error"))
        self.assertEqual(self.pan_calls(calls), [],
                         "没有任何站填网页搜索地址时不该调用 pan_search")
        lines = job.get("lines") or []
        self.assertTrue(any("云盘通道没有入口" in line for line in lines),
                        f"应当明说没有入口，而不是静默返回空：{lines}")


if __name__ == "__main__":
    unittest.main()
