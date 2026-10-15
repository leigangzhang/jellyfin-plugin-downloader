"""L1 搜索「中途被杀不白跑」的契约 + 本轮三条读侧/状态收口（t18）。

来源：t15 审查（/tmp/jmd-audit/t15-review.md）的 F-T15-1..F-T15-5。

全部离线：网络入口一律换成假 search 或打桩，`backend/state/` 既不读也不写。

* `InterruptedLadderTests` —— N3：每完成一个关键词就原子落盘，第二个关键词抛
  `KeyboardInterrupt`（等价于控制台 600s 硬杀 rc=124）时，第一个关键词的池条目与
  候选磁力必须还在文件里。这条用例在修复前会红（t15 的零联网复现：两个文件都不存在）。
* `PoolMetadataCacheMissTests` —— F-T15-2：`videos` 为空 / `main_video_gb <= 0`
  的池条目即使带着新鲜的 `metadata_at` 也算缓存未命中。
* `ConsoleRoundStatusTests` —— F-T15-4：本轮状态（partial/timeout/round_empty）落进
  context.json，从磁盘重建的快照给出同一结论，空轮把候选标成上一轮。
* `ProbeErrorSurfacingTests` —— F-T15-5：worker 里冒出来的真实异常进 `rejected[].error`。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
FIXTURES = ROOT / "tests" / "fixtures" / "jmd_probe"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from probe import probe_magnets as pm  # noqa: E402
from search import search_pool as sp  # noqa: E402
import source_config  # noqa: E402

# 中文片名 + 中文整包词：search_pool 的阶梯有「中英不混搭」规则，混搭会得到空 plan。
TITLE = "假标题"
HINT_FIRST = "全集"
HINT_SECOND = "第1季"
HASH_FIRST = "A" * 40


def candidate_item(info_hash=HASH_FIRST, name="Fake.Title.2024.1080p.WEB-DL.x264-TEST"):
    """一条索引记录的形状（diao.im 的字段名）。"""
    return {
        "hash": info_hash,
        "name": name,
        "size": "8053063680",
        "files_count": "1",
        "files": [{"index": 0, "path": name + ".mkv", "size": 8053063680}],
        "magnet_uri": f"magnet:?xt=urn:btih:{info_hash}",
        "created_at": "1",
    }


def page(info_hash=HASH_FIRST):
    return {"status": 200, "has_more": False, "torrents": [candidate_item(info_hash)]}


def magnets_in(path):
    if not os.path.exists(path):
        return None
    return [
        line for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


class SearchBudgetCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jmd-search-budget-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pool = os.path.join(self.tmp, "pool.json")
        self.candidates = os.path.join(self.tmp, "candidates.magnets")
        self.json_out = os.path.join(self.tmp, "search.json")

    def run_main(self, fake_search):
        """跑真实的 search_pool.main()，只把「索引请求」换成 fake_search。

        注意 `fetch_query_pages(query, ..., search=search_api)` 的 `search` 是**定义时**
        默认参数，所以 `mock.patch.object(sp, "search_api")` 打不到 pager；这里包一层
        `fetch_query_pages` 把 `search` 显式换成假函数（t15 用的同一手法）。
        """
        real_fetch = sp.fetch_query_pages

        def fake_fetch(query, **kwargs):
            return real_fetch(query, search=fake_search, **kwargs)

        argv = [
            "search_pool.py", TITLE,
            "--strict-match",
            "--collection-hint", HINT_FIRST,
            "--collection-hint", HINT_SECOND,
            "--max-queries", "2",
            "--pool-file", self.pool,
            "--candidates-out", self.candidates,
            "--json-out", self.json_out,
        ]
        stdout = io.StringIO()
        # 只留一个站、并把锚点档显式关掉（空数组）：本用例只考察 L1 阶梯的落盘，
        # 站点清单与关键字配置都不该改变它的查询序列。
        env = {
            source_config.ENV_SITES: json.dumps([source_config.DEFAULT_SITES[0]]),
            source_config.ENV_KEYWORDS: "[]",
        }
        with mock.patch.object(sp, "fetch_query_pages", fake_fetch), \
             mock.patch.object(sys, "argv", argv), \
             mock.patch.dict(os.environ, env, clear=False), \
             redirect_stdout(stdout):
            rc = sp.main()
        return rc, stdout.getvalue()

    def pool_entry(self, info_hash=HASH_FIRST):
        payload = json.loads(Path(self.pool).read_text(encoding="utf-8"))
        return payload["entries"].get(info_hash) or {}


class InterruptedLadderTests(SearchBudgetCase):
    def test_first_keyword_survives_an_interrupt_on_the_second(self):
        """第二个关键词被打断时，第一个关键词的池条目与磁力必须已经落盘。

        修复前会看到：`pool.json` 与 `candidates.magnets` 都不存在（t15 的复现）。
        """
        seen = []

        def fake_search(query, limit=10, offset=0, base_url="", timeout=20):
            seen.append(query)
            if HINT_SECOND in query:
                raise KeyboardInterrupt("simulated rc=124")
            return page()

        with self.assertRaises(KeyboardInterrupt):
            self.run_main(fake_search)

        self.assertEqual(len(seen), 4 + 4, f"阶梯应跑到第二个关键词才被打断：{seen}")
        self.assertTrue(os.path.exists(self.pool), "池文件不存在：中途被杀把已完成部分丢了")
        self.assertTrue(
            os.path.exists(self.candidates), "候选磁力文件不存在：probe 会看不到任何候选"
        )
        entry = self.pool_entry()
        self.assertEqual(entry.get("main_video_gb"), 7.5, "第一个关键词的候选没进池")
        self.assertIn("metadata_at", entry, "渐进落盘时没给带清单的候选盖 metadata_at")
        self.assertEqual(
            magnets_in(self.candidates),
            [f"magnet:?xt=urn:btih:{HASH_FIRST}"],
            "候选磁力没落地（probe 只从这个文件读候选）",
        )
        self.assertEqual(
            [name for name in os.listdir(self.tmp) if name.endswith(".tmp")], [],
            "原子写留下了 .tmp 残骸",
        )

    def test_a_complete_round_lands_everything_without_tmp_leftovers(self):
        """正常跑完：两个关键词的候选都在池里，且没有 .tmp 残骸。"""
        rc, _ = self.run_main(lambda query, **kw: page())
        self.assertEqual(rc, 0)
        payload = json.loads(Path(self.pool).read_text(encoding="utf-8"))
        self.assertIn(HASH_FIRST, payload["entries"])
        self.assertEqual(magnets_in(self.candidates), [f"magnet:?xt=urn:btih:{HASH_FIRST}"])
        self.assertEqual(
            [name for name in os.listdir(self.tmp) if name.endswith(".tmp")], []
        )
        self.assertTrue(os.path.exists(self.json_out), "--json-out 正常路径仍要写")

    def test_progress_landing_is_atomic_and_repeatable(self):
        """连续两次落盘（每关键词一次 + 收尾一次）内容一致、不互踩。"""
        calls = []
        real_land = sp.land_progress

        def recorder(args, pool, candidates, reports, now=None):
            calls.append(len(candidates))
            return real_land(args, pool, candidates, reports, now=now)

        with mock.patch.object(sp, "land_progress", recorder):
            rc, _ = self.run_main(lambda query, **kw: page())
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(len(calls), 3, f"每个关键词 + 收尾都要落盘：{calls}")


class PoolMetadataCacheMissTests(unittest.TestCase):
    """F-T15-2：空文件清单 + 新鲜戳 = 缓存未命中（与写入侧同一判据）。"""

    def setUp(self):
        self.now = time.time()
        self.info_hash = "B" * 40

    def entry(self, videos, main_video_gb, stamp=None):
        return {
            "name": "Whatever.2024.1080p.WEB-DL.x264-TEST.mkv",
            "size_gb": 7.5,
            "nfiles": len(videos),
            "videos": videos,
            "main_video_gb": main_video_gb,
            "main_video_ext": ".mkv",
            "junk_files": 0,
            "metadata_at": self.now - 60 if stamp is None else stamp,
        }

    def test_empty_file_list_with_a_fresh_stamp_is_a_cache_miss(self):
        pool = {self.info_hash: self.entry([], 0.0)}
        self.assertIsNone(
            pm.pool_metadata(pool, self.info_hash, self.now),
            "空清单仍被当命中 → 这一轮会直接以「没有可读视频」剔掉候选，连抓都不抓",
        )

    def test_usable_file_list_still_hits(self):
        videos = [{"index": 0, "path": "x.mkv", "name": "x.mkv", "size": 8053063680}]
        pool = {self.info_hash: self.entry(videos, 7.5)}
        cached = pm.pool_metadata(pool, self.info_hash, self.now)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["main_video_gb"], 7.5)

    def test_stale_stamp_is_still_a_cache_miss(self):
        videos = [{"index": 0, "path": "x.mkv", "name": "x.mkv", "size": 8053063680}]
        pool = {self.info_hash: self.entry(videos, 7.5, stamp=self.now - 8 * 24 * 3600)}
        self.assertIsNone(pm.pool_metadata(pool, self.info_hash, self.now))


class ConsoleRoundStatusTests(unittest.TestCase):
    """F-T15-4：本轮状态落盘，磁盘重建的快照与 job 内快照结论一致。"""

    PREVIOUS = {
        "query": "previous",
        "kind": "movie",
        "ranked": [
            {
                "rank": 1, "ih": "F" * 40, "name": "Previous.Round.Candidate",
                "score": 88.0, "seeds": 10, "breakdown": {},
                "dimensions": {"quality": 90.0, "speed": 90.0, "experience": 80.0, "fit": 80.0},
            }
        ],
        "best": {"ih": "F" * 40, "name": "Previous.Round.Candidate", "score": 88.0},
        "rejected": [], "policy": {}, "min_score": 45,
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jmd-round-status-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.snapshots = os.path.join(self.tmp, "snapshots")
        os.makedirs(self.snapshots)
        import console_server as cs  # 懒加载：控制台栈的入口
        import build_console as bc

        self.cs, self.bc = cs, bc
        self.probe_path = os.path.join(self.snapshots, "demo.probe.json")
        with open(self.probe_path, "w", encoding="utf-8") as handle:
            json.dump(self.PREVIOUS, handle, ensure_ascii=False)
        with open(os.path.join(self.snapshots, "demo.context.json"), "w", encoding="utf-8") as handle:
            json.dump({"title": "Demo", "kind": "movie", "search_status": "ok"}, handle)
        # 上一轮的 mtime：用 os.utime 显式放到过去，不靠 sleep 等时间流逝
        # （固定等待是被 no-time-based-test-sync 禁止的测试写法）。
        past = time.time() - 120
        os.utime(self.probe_path, (past, past))
        self.params = {
            "title": "Demo", "kind": "movie", "category": "Movies", "slug": "demo",
            "key": "demo", "duration": 120.0, "year": "", "alt_title": [],
            "season": None, "episode": None,
            "sources": "magnet", "original_title": "", "strict_season": True,
            "final_dir": self.tmp, "probe_kind": "movie",
            "pool_file": os.path.join(self.tmp, "pool.json"),
            "cand_file": os.path.join(self.tmp, "cands.magnets"),
            "probe_out": self.probe_path,
        }

    def run_round(self, stream_result):
        patchers = [
            mock.patch.object(self.bc, "SNAPSHOTS_DIR", self.snapshots),
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

    def read_context(self):
        path = os.path.join(self.snapshots, "demo.context.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_empty_round_status_survives_a_disk_rebuild(self):
        line = 'PROBE_PARTIAL {"timeout_stage": "metadata", "expired_stages": ["metadata"]}'
        job = self.run_round(lambda *a, **k: (4, [line, "NO_ELIGIBLE_RESOURCE"]))

        ctx = self.read_context()
        self.assertEqual(ctx["search_status"], "probe_partial")
        self.assertTrue(ctx["probe_partial"])
        self.assertEqual(ctx["probe_timeout_stage"], "metadata")
        self.assertTrue(ctx["probe_round_empty"])

        rebuilt = self.cs._build_snapshot("demo")
        self.assertTrue(rebuilt["probe_partial"], "磁盘重建的快照丢了 partial 结论")
        self.assertEqual(rebuilt["probe_timeout_stage"], "metadata")
        self.assertTrue(rebuilt["probe_round_empty"])
        self.assertEqual(
            rebuilt["search_status"], job["search_status"],
            "job 内快照与磁盘重建快照的结论必须一致",
        )
        self.assertTrue(
            all(c.get("previous_round") for c in rebuilt["candidates"]),
            "空轮时磁盘上那份候选是上一轮的，必须标出来",
        )
        # 更严格的一条：绕开 console 的包装，直接让 build_snapshot 从磁盘重建
        # （它没拿到显式传参时必须回落到 context.json，否则任何一次重建快照都会得到
        # probe_partial=False + 上一轮 candidates 的自相矛盾）。
        direct = self.bc.build_snapshot("demo")
        self.assertTrue(direct["probe_partial"], "build_snapshot(slug) 没从 context 取回 partial")
        self.assertEqual(direct["probe_timeout_stage"], "metadata")
        self.assertTrue(direct["probe_round_empty"])
        self.assertTrue(all(c.get("previous_round") for c in direct["candidates"]))

    def test_a_normal_round_is_not_marked_as_previous(self):
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

        self.run_round(stream)
        ctx = self.read_context()
        self.assertFalse(ctx["probe_partial"])
        self.assertFalse(ctx["probe_round_empty"])
        rebuilt = self.cs._build_snapshot("demo")
        self.assertFalse(rebuilt["probe_round_empty"])
        self.assertEqual([c["name"] for c in rebuilt["candidates"]], ["Fresh.Result"])
        self.assertNotIn("previous_round", rebuilt["candidates"][0])


class ProbeErrorSurfacingTests(unittest.TestCase):
    """F-T15-5：worker 里冒出来的真实异常要能看见，不能只留一句固定文案。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jmd-probe-error-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = time.time()
        self.ok_hash = "C" * 40
        self.boom_hash = "D" * 40

    def write_inputs(self):
        videos = [{"index": 0, "path": "ok.mkv", "name": "ok.mkv", "size": 8053063680}]
        entries = {
            self.ok_hash: {
                "name": "Fine.2024.1080p.WEB-DL.x264-TEST.mkv", "size_gb": 7.5, "nfiles": 1,
                "videos": videos, "main_video_gb": 7.5, "main_video_ext": ".mkv",
                "junk_files": 0, "junk_bytes": 0,
                "metadata_at": self.now - 60, "health_at": self.now - 60,
                "seeds": 30, "peers": 40, "external_peers": 40, "health_method": "announce",
            },
            self.boom_hash: {
                "name": "Explodes.2024.1080p.WEB-DL.x264-TEST.mkv", "size_gb": 7.5, "nfiles": 1,
                "videos": [], "main_video_gb": 0.0, "main_video_ext": "",
                "junk_files": 0, "junk_bytes": 0,
                # 冷 metadata → enrich 必须联网；health 新鲜 → 不碰 tracker
                "metadata_at": 0, "health_at": self.now - 60,
                "seeds": 30, "peers": 40, "external_peers": 40, "health_method": "announce",
            },
        }
        pool = os.path.join(self.tmp, "pool.json")
        with open(pool, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "query": "x", "updated_at": "x", "entries": entries}, handle)
        cands = os.path.join(self.tmp, "cands.txt")
        Path(cands).write_text(
            f"magnet:?xt=urn:btih:{self.ok_hash}\nmagnet:?xt=urn:btih:{self.boom_hash}\n",
            encoding="utf-8",
        )
        return pool, cands

    def test_worker_exception_reaches_rejected_error(self):
        pool, cands = self.write_inputs()
        out = os.path.join(self.tmp, "probe.json")
        argv = ["probe_magnets.py", "x", "--kind", "movie", "--duration-minutes", "120",
                "--candidate-file", cands, "--pool-file", pool, "--json-out", out,
                "--workers", "2", "--no-live-check"]

        def exploding_fetch(info_hash, timeout, deadline=None):
            if info_hash == self.boom_hash:
                raise RuntimeError("aria2c vanished")
            return None

        with mock.patch.object(pm, "fetch_metadata", exploding_fetch), \
             mock.patch.object(pm, "announce_health", lambda *a, **k: (_ for _ in ()).throw(
                 AssertionError("不应联网 announce"))), \
             mock.patch.object(pm, "public_ip", lambda: ""):
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                rc = pm.main()

        self.assertEqual(rc, 0, "另一个候选应当正常交付")
        with open(out, encoding="utf-8") as handle:
            probe = json.load(handle)
        rejected = {item["ih"]: item for item in probe["rejected"]}
        self.assertIn(self.boom_hash, rejected)
        self.assertEqual(rejected[self.boom_hash]["error"], "aria2c vanished")
        self.assertTrue(rejected[self.boom_hash]["reason"].startswith("metadata unavailable"))


if __name__ == "__main__":
    unittest.main()
