"""probe 的软预算、进度落点、live-check 与 metadata 复用（全部离线）。

拆自 `tests/backend_probe/test_backend_probe.py`（按主题），共享件在
`tests/backend_probe/_backend_probe_helpers.py`。"""

from __future__ import annotations

import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.backend_probe._backend_probe_helpers import (
    BUDGET_SLACK_SECONDS,
    ProbeCase,
    offline_network_guard,
    pm,
)

class SoftBudgetTests(ProbeCase):
    def test_tiny_budget_lands_partial_json_without_any_network_call(self):
        """--budget-secs 1：JSON 仍在、带 partial/timeout_stage，且不做任何网络调用。"""
        pool_path = self.pool_file()
        patchers = offline_network_guard(self)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        started = time.monotonic()
        rc, out_path, stdout = self.run_probe(pool_path, ["--budget-secs", "1"])
        wall = time.monotonic() - started

        self.assertEqual(rc, 0)
        probe = self.read_json(out_path)
        self.assertTrue(probe["partial"])
        self.assertTrue(probe["timeout_stage"], "partial 必须说明是哪个阶段超时")
        self.assertEqual(probe["completed"]["metadata"], 3, "缓存命中的候选应全部完成")
        self.assertIn("PROBE_PARTIAL", stdout)
        # 阶段墙钟都远小于预算；真实网络调用（≥0.5s，metadata ≥6s）会立刻打破这个界限。
        for stage, seconds in probe["stage_seconds"].items():
            self.assertLessEqual(
                seconds, 1.0 + BUDGET_SLACK_SECONDS,
                f"{stage} 阶段墙钟 {seconds}s 超出 1s 预算太多",
            )
        self.assertLessEqual(wall, 1.0 + BUDGET_SLACK_SECONDS)
        self.assert_no_json_staging_left(pool_path)

    def test_tiny_budget_skips_uncached_metadata_instead_of_half_running_it(self):
        """冷池 + 1s 预算：不发起注定超时的 metadata 抓取，池仍然落盘。"""
        pool_path = self.pool_file(fresh_metadata=False)
        patchers = offline_network_guard(self)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        started = time.monotonic()
        rc, out_path, stdout = self.run_probe(pool_path, ["--budget-secs", "1"])
        wall = time.monotonic() - started

        # 没有可交付候选时沿用旧行为：写池、不写 --json-out、退出码 4（语义未变），
        # 但 stdio 里有 PROBE_PARTIAL 说明真实原因。
        self.assertEqual(rc, 4)
        self.assertFalse(os.path.exists(out_path))
        self.assertIn("PROBE_PARTIAL", stdout)
        self.assertLessEqual(wall, 1.0 + BUDGET_SLACK_SECONDS)
        pool = json.loads(Path(pool_path).read_text(encoding="utf-8"))
        self.assertEqual(len(pool["entries"]), 3)

    def test_budget_zero_keeps_unlimited_behaviour(self):
        """默认（--budget-secs 0）不产生 partial 标记，且不记录任何阶段超时。"""
        pool_path = self.pool_file()
        rc, out_path, _ = self.run_probe(pool_path, ["--no-live-check"])
        self.assertEqual(rc, 0)
        probe = self.read_json(out_path)
        self.assertFalse(probe["partial"])
        self.assertEqual(probe["timeout_stage"], "")
        self.assertEqual(probe["expired_stages"], [])

    def test_budget_out_of_time_records_stage_without_expiring(self):
        """out_of_time 在预算还没归零、但已不够跑该阶段时也要记账。"""
        clock = [100.0]
        budget = pm.SoftBudget(30.0, clock=lambda: clock[0])
        self.assertFalse(budget.out_of_time("metadata", pm.MIN_METADATA_BUDGET_SECONDS))
        clock[0] += 26.0  # 剩 4s < 5s：不够跑一次 metadata
        self.assertTrue(budget.out_of_time("metadata", pm.MIN_METADATA_BUDGET_SECONDS))
        self.assertEqual(budget.timeout_stage, "metadata")
        self.assertEqual(budget.expired_stages, ["metadata"])
        self.assertEqual(budget.remaining(), 4.0)


class ProgressLandingTests(ProbeCase):
    def test_pool_is_landed_after_every_enriched_candidate(self):
        """metadata 阶段每完成一条就落盘一次，而不是只在末尾落一次。"""
        pool_path = self.pool_file()
        saved = []
        real_save_progress = pm._save_progress

        def recorder(*args, **kwargs):
            saved.append(time.monotonic())
            return real_save_progress(*args, **kwargs)

        with mock.patch.object(pm, "_save_progress", recorder):
            rc, out_path, _ = self.run_probe(pool_path, ["--no-live-check"])

        self.assertEqual(rc, 0)
        self.assertGreaterEqual(
            len(saved), 3,
            "3 个候选都走 metadata 阶段，逐条落盘至少应该发生 3 次",
        )
        self.assert_no_json_staging_left(pool_path)

    def test_json_out_is_written_atomically(self):
        """--json-out 走临时文件 + os.replace：写完不留 .tmp，内容可解析。"""
        pool_path = self.pool_file()
        rc, out_path, _ = self.run_probe(pool_path, ["--no-live-check"])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(out_path + ".tmp"))
        self.assertTrue(self.read_json(out_path)["ranked"])


class LiveCheckTests(ProbeCase):
    def _args(self, **kwargs):
        args = mock.Mock()
        args.live_check_top = 3
        args.live_check_ttl = 3600
        args.timeout = 25
        for key, value in kwargs.items():
            setattr(args, key, value)
        return args

    def _candidate(self, info_hash):
        return {
            "ih": info_hash,
            "name": "Fixture.Movie.Alpha.2024.1080p.WEB-DL.x264-TEST.mkv",
            "kind": "magnet",
            "external_peers": 50,
        }

    def test_live_check_reuses_metadata_fetched_in_the_same_round(self):
        """本轮 enrich 刚抓到的 metadata 就是这次 live 证明，不再抓第二次。"""
        info_hash = "1" * 40
        pool = {info_hash: {"name": "x", "metadata_at": self.now}}
        ranked = [self._candidate(info_hash)]
        with mock.patch.object(pm, "fetch_metadata") as fetch:
            summary = pm.verify_live(
                ranked, pool, self.now, self._args(), round_started_at=self.now
            )
        fetch.assert_not_called()
        self.assertEqual(summary, {"checked": 0, "reused_from_enrich": 1})
        self.assertTrue(ranked[0]["metadata_verified"])
        self.assertFalse(ranked[0].get("dead_swarm", False))
        self.assertEqual(pool[info_hash]["probe_ok_at"], self.now)

    def test_live_check_fetches_once_for_metadata_from_an_older_round(self):
        """池里的旧 metadata 不能当本轮证明：抓一次；抓完第二轮不再抓。"""
        info_hash = "2" * 40
        pool = {info_hash: {"name": "x", "metadata_at": self.now - 86400}}
        ranked = [self._candidate(info_hash)]
        args = self._args()
        with mock.patch.object(
            pm, "fetch_metadata", return_value={"name": "x"}
        ) as fetch:
            first = pm.verify_live(
                ranked, pool, self.now, args, round_started_at=self.now
            )
            ranked2 = [self._candidate(info_hash)]
            second = pm.verify_live(
                ranked2, pool, self.now, args, round_started_at=self.now
            )
        self.assertEqual(fetch.call_count, 1, "同一轮里不允许对同一 hash 抓两次")
        self.assertEqual(first["checked"], 1)
        self.assertEqual(second, {"checked": 0, "reused_from_enrich": 0})
        self.assertTrue(ranked2[0]["metadata_verified"])

    def test_live_check_stops_when_the_budget_cannot_afford_a_fetch(self):
        """剩余预算不够跑一次 metadata 抓取时，live-check 直接收工并把阶段记账。"""
        info_hash = "3" * 40
        pool = {info_hash: {"name": "x", "metadata_at": self.now - 86400}}
        ranked = [self._candidate(info_hash)]
        budget = pm.SoftBudget(1.0)
        with mock.patch.object(pm, "fetch_metadata") as fetch:
            summary = pm.verify_live(
                ranked, pool, self.now, self._args(),
                round_started_at=self.now, budget=budget,
            )
        fetch.assert_not_called()
        self.assertEqual(summary["checked"], 0)
        self.assertEqual(budget.timeout_stage, "live-check")


class MetadataFetchTests(ProbeCase):
    def _capture_argv(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

        return captured, fake_run

    def test_fetch_metadata_keeps_dht_enabled(self):
        """DHT 是 metadata 的实际来源（关掉后 30s 拿不到），命令行必须显式打开。"""
        captured, fake_run = self._capture_argv()
        with mock.patch.object(pm.subprocess, "run", side_effect=fake_run):
            pm.fetch_metadata("4" * 40, 5)
        self.assertIn(pm.ARIA2_DHT_FLAG, captured["cmd"])
        self.assertEqual(pm.ARIA2_DHT_FLAG, "--enable-dht=true")

    def test_fetch_metadata_clamps_the_hard_kill_to_the_budget_deadline(self):
        """软预算的 deadline 会把 timeout+10s 的硬杀上限压下来。"""
        captured, fake_run = self._capture_argv()
        deadline = time.monotonic() + 2.0
        with mock.patch.object(pm.subprocess, "run", side_effect=fake_run):
            self.assertIsNone(pm.fetch_metadata("5" * 40, 25, deadline=deadline))
        self.assertLessEqual(captured["kwargs"]["timeout"], 2.5)
        self.assertGreaterEqual(captured["kwargs"]["timeout"], 1.0)


class TrackerListTests(unittest.TestCase):
    # 2026-10-13 实测：可达的只有这三台（announce 均 <0.9s）。
    REACHABLE_ANNOUNCE = {
        "http://tracker.opentrackr.org:1337/announce",
        "http://bt1.archive.org:6969/announce",
        "http://tracker2.dler.org:80/announce",
    }
    # 实测不可用：8s 超时 / HTTP 403 / 连接被拒。留着就是把每条候选的最坏等待拉长。
    DEAD = {
        "http://explodie.org:6969/announce",
        "http://tracker.openbittorrent.com:80/announce",
        "http://tracker.torrent.eu.org:451/announce",
    }

    def test_announce_trackers_are_only_the_verified_reachable_ones(self):
        self.assertEqual(set(pm.ANNOUNCE_TRACKERS), self.REACHABLE_ANNOUNCE)
        self.assertFalse(self.DEAD & set(pm.ANNOUNCE_TRACKERS))

    def test_scrape_trackers_are_reachable_and_actually_answer(self):
        """bt1.archive.org 的 scrape 返回空 files 字典，不能进 HTTP_TRACKERS。"""
        self.assertEqual(
            set(pm.HTTP_TRACKERS),
            {
                "http://tracker.opentrackr.org:1337/scrape",
                "http://tracker2.dler.org:80/scrape",
            },
        )
        self.assertNotIn("http://bt1.archive.org:6969/scrape", pm.HTTP_TRACKERS)

    def test_every_announce_tracker_uses_http(self):
        """announce_health 走 urllib，只能吃 HTTP(S) 端点；UDP 会静默失败。"""
        for tracker in pm.ANNOUNCE_TRACKERS:
            self.assertTrue(tracker.startswith("http://"), tracker)

