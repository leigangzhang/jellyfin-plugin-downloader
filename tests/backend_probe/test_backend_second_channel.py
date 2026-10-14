"""第二通道接线：`torrents_csv_source` 必须真的被 `search_pool` 调用，且外部种子数可见。

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
    FakeIndexHttp,
    IntegrationCase,
    cs,
    diao_index_page,
    sp,
    tcs,
)


class SecondChannelWiringTests(IntegrationCase):
    """torrents_csv_source 必须真的被 search_pool 接线调用，且外部种子数可见。"""

    def _external_entry(self, **overrides):
        entry = {
            "info_hash": self.EXTERNAL_HASH,
            "name": "External.Only.2024.1080p.WEB-DL.x264-TEST",
            "size": 8053063680, "size_gb": 7.5, "file_count": None,
            "seeders": 18, "leechers": 4, "created_at": None,
            "seeders_snapshot_unix": int(self.now - 22 * 86400),
            "seeders_snapshot_at": "2026-01-01T00:00:00+00:00",
            "seeders_snapshot_age_days": 22.0,
            "sources": ["torrents-csv"],
            "magnet": f"magnet:?xt=urn:btih:{self.EXTERNAL_HASH}",
        }
        entry.update(overrides)
        return entry

    def _run_search_pool(self, pool_payload, entries, extra_argv=()):
        pool_path = self._write_json("wire-pool.json", pool_payload)
        cand_out = os.path.join(self.tmp, "wire.magnets")
        argv = ["search_pool.py", "External Only", "--pool-file", pool_path,
                "--candidates-out", cand_out,
                "--json-out", os.path.join(self.tmp, "wire-search.json"),
                *extra_argv]
        index_http = FakeIndexHttp(diao_index_page())
        result = {"status": 200, "message": "", "query": "", "elapsed_secs": 0.1,
                  "entries": entries}
        calls = []

        def fake_external(query, **kwargs):
            calls.append(query)
            return dict(result)

        # L1 侧走真实的 `search_api`（它才是被测对象），但把它的 HTTP 边界换成桩：
        # 打 `sp.search_api` 无效（`fetch_query_pages` 的定义时默认参数），见 FakeIndexHttp。
        with mock.patch.object(sp.urllib.request, "urlopen", index_http), \
             mock.patch.object(tcs, "search", side_effect=fake_external):
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                rc = sp.main()
        self.assertTrue(
            index_http.requests,
            "L1 索引一次都没被问到：桩没打在真正的 HTTP 边界上（t13 F-1 会复现）",
        )
        return rc, pool_path, cand_out, calls, index_http

    def test_search_pool_calls_the_second_channel_and_records_seeders(self):
        rc, pool_path, cand_out, calls, index_http = self._run_search_pool(
            {"version": 1, "query": "x", "updated_at": "x", "entries": {}},
            [self._external_entry()],
        )
        self.assertEqual(calls, ["External Only"], "第二通道必须被调用一次")
        self.assertTrue(index_http.requests, "L1 索引必须被真桩问过（离线）")
        pool = self.read_json(pool_path)["entries"]
        self.assertIn(self.EXTERNAL_HASH, pool)
        entry = pool[self.EXTERNAL_HASH]
        self.assertEqual(entry["external_seeders"], 18)
        self.assertEqual(entry["external_sources"], ["torrents-csv"])
        self.assertEqual(entry["external_seeders_age_days"], 22.0)
        # 第二通道的磁力必须写进 probe 消费的候选文件
        magnets = [line for line in Path(cand_out).read_text(encoding="utf-8").splitlines()
                   if line and not line.startswith("#")]
        self.assertIn(f"magnet:?xt=urn:btih:{self.EXTERNAL_HASH}", magnets)
        self.assertEqual(rc, 0)

    def test_legacy_seeder_field_names_are_not_scoring_inputs(self):
        """旧字段名不是打分输入；新字段名（`external_seeders`）在 announce 无读数时会。

        t13 复核把这件事拆成两半，这里两条都钉住：
          * 旧的 `seeders` / `seeders_snapshot_age_days` 只活在 `torrents_csv_source`
            的条目形状里；`search_pool.torrents_csv_to_candidate` 已经把池侧的名字
            改成 `external_seeders` / `external_seeders_age_days`。打分侧只认新名，
            所以旧名的任何取值（含快照 22 天、5000 个种子）与**不带该字段逐位同分**。
          * 新名确实会被消费：`seeds<=0 and peers<=0`（announce 完全没有读数）时，
            `external_seeders` 改变总分；announce 有读数时它不参与（先验用 announce）。
            这条按 t13 的结论保留，不再是「被忽略」的说法。
        """
        policy = cs.normalize_policy({"kind": "movie", "duration_seconds": 7200,
                                      "seeds_reference": 120.0, "peers_reference": 200.0})
        base = {
            "ih": "E" * 40, "name": "X.2024.1080p.WEB-DL.x264-TEST.mkv",
            "kind": "magnet", "seeds": 30, "peers": 40,
            "videos": [{"index": 0, "path": "x.mkv", "name": "x.mkv", "size": 8053063680}],
            "main_video_gb": 7.5, "size_gb": 7.5, "nfiles": 1, "main_video_ext": ".mkv",
            "junk_files": 0, "junk_bytes": 0,
        }
        reference = cs.score_candidate(dict(base), policy)["score"]
        legacy_variants = {
            "seeders=18": {"seeders": 18},
            "seeders=0": {"seeders": 0},
            "seeders=None": {"seeders": None},
            "snapshot 22d old": {"seeders": 18, "seeders_snapshot_age_days": 22.0},
            "stale but 5000": {"seeders": 5000, "seeders_snapshot_age_days": 30.0},
        }
        for label, extra in legacy_variants.items():
            with self.subTest(variant=label):
                cand = dict(base, **extra)
                self.assertAlmostEqual(
                    cs.score_candidate(cand, policy)["score"], reference, delta=1e-9,
                    msg=f"旧字段名 {label} 改变了总分 → 旧名被当成了打分输入",
                )

        # 新名：announce 有读数时不许插手（先验来自 announce）。
        self.assertAlmostEqual(
            cs.score_candidate(
                dict(base, external_seeders=5000, external_seeders_age_days=3.0), policy
            )["score"],
            reference, delta=1e-9,
            msg="announce 有读数时 external_seeders 不应改变总分",
        )
        # 新名：announce 完全没有读数时必须被消费（否则就是 F4 断链复现）。
        silent = dict(base, seeds=0, peers=0)
        silent_reference = cs.score_candidate(dict(silent), policy)["score"]
        boosted = cs.score_candidate(
            dict(silent, external_seeders=5000, external_seeders_age_days=3.0), policy
        )["score"]
        self.assertGreater(
            boosted, silent_reference,
            "announce 无读数时 external_seeders 没有改变总分 → 新字段没被消费",
        )

    def test_external_seeders_do_not_override_a_dead_swarm_verdict(self):
        """`dead_swarm` 是可达性判决，优先于先验：外部种子数一律被忽略（刻意设计）。

        复现面：announce 报 `external_peers == 0` → `dead_swarm=True`，只要不跑
        live-check（`--no-live-check`），candidate_score 直接给 0.05，外部 5000 也
        救不回来。生产控制台不传 `--no-live-check`，默认流程里这类候选会先被
        live-check 的 metadata 复核救回（metadata_verified=True → dead_swarm=False）。
        这条只钉语义，不改优先级（t13 F-4）。
        """
        policy = cs.normalize_policy({"kind": "movie", "duration_seconds": 7200})
        candidate = {
            "name": "X.2024.1080p.WEB-DL.x264-TEST.mkv", "kind": "magnet",
            "seeds": 0, "peers": 0, "dead_swarm": True,
            "videos": [{"index": 0, "path": "x.mkv", "name": "x.mkv", "size": 8053063680}],
            "main_video_gb": 7.5, "size_gb": 7.5, "nfiles": 1, "main_video_ext": ".mkv",
            "junk_files": 0, "junk_bytes": 0,
            "external_seeders": 5000, "external_seeders_age_days": 0.0,
        }
        result = cs.score_candidate(candidate, policy)
        self.assertEqual(result["breakdown"]["speed"]["mode"], "dead-swarm")
        self.assertAlmostEqual(result["dimensions"]["speed"], 5.0)
