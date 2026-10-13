"""probe_magnets 的离线契约测试：软预算、partial 落盘、live-check 复用 metadata。

这些测试全部离线（不碰网络、不碰 backend/state/）：网络入口
（`announce_health` / `tracker_health` / `fetch_metadata` / `public_ip`）在用例里被
替换成「一被调用就失败」的桩，所以任何一次真实网络调用都会把用例打红，而不是悄悄
通过。aria2c 测速入口已删除，所以这里没有它的桩。

夹具在 tests/fixtures/jmd_probe/：合成的 info hash + 合成 pool，与任何真实种子无关。
golden_ranked.json 是**改动前**用同一份夹具跑出来的排名，用来钉住「正常路径的
排名不变」。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FIXTURES = ROOT / "tests" / "fixtures" / "jmd_probe"

# backend/ 不是包，脚本之间靠同目录 import；这里补上路径，让测试能加载被测模块。
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import probe_magnets as pm  # noqa: E402

# 预算断言的容差：只覆盖解释器启动与调度抖动，不足以掩盖一次真实网络调用
# （metadata 实测 6.2–8.4s，announce 0.5–0.9s）。
BUDGET_SLACK_SECONDS = 2.0


def offline_network_guard(test_case):
    """把每个网络入口换成「被调用即失败」的桩，返回 patch 列表。"""
    calls = []

    def boom(name):
        def _inner(*args, **kwargs):
            calls.append(name)
            test_case.fail(f"离线测试里发生了真实网络调用：{name}{args!r}")
        return _inner

    return [
        mock.patch.object(pm, "announce_health", boom("announce_health")),
        mock.patch.object(pm, "tracker_health", boom("tracker_health")),
        mock.patch.object(pm, "fetch_metadata", boom("fetch_metadata")),
        mock.patch.object(pm, "public_ip", boom("public_ip")),
    ]


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jmd-probe-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = time.time()

    def pool_file(self, fresh_health=True, fresh_metadata=True):
        """把夹具 pool 拷进临时目录，按需把 TTL 字段写成「刚过期一点点」。

        `fresh_metadata=False` 模拟冷池：没有 metadata_at → metadata 阶段必须联网。
        """
        payload = json.loads((FIXTURES / "pool.json").read_text(encoding="utf-8"))
        for entry in payload["entries"].values():
            entry["metadata_at"] = self.now - 60 if fresh_metadata else 0
            entry["health_at"] = self.now - 60 if fresh_health else 0
        path = os.path.join(self.tmp, "pool.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return path

    def run_probe(self, pool_path, extra_args=(), argv_patches=None):
        out_path = os.path.join(self.tmp, "probe.json")
        argv = [
            "probe_magnets.py", "fixture",
            "--kind", "movie",
            "--duration-minutes", "120",
            "--candidate-file", str(FIXTURES / "candidates.txt"),
            "--pool-file", pool_path,
            "--json-out", out_path,
            "--workers", "2",
            *extra_args,
        ]
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(stdout):
            rc = pm.main()
        return rc, out_path, stdout.getvalue()

    def read_json(self, path):
        self.assertTrue(os.path.exists(path), f"{path} 不存在")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def assert_no_json_staging_left(self, pool_path):
        for name in os.listdir(self.tmp):
            self.assertFalse(
                name.endswith(".tmp"),
                f"原子写的临时文件没被 os.replace 收走：{name}",
            )
        self.assertEqual(sorted(json.loads(Path(pool_path).read_text(encoding="utf-8"))),
                         ["entries", "query", "updated_at", "version"])


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


class ConsoleWiringTests(unittest.TestCase):
    """console_server 侧的接线，用源码断言（导入它会拉起整个控制台栈）。"""

    def setUp(self):
        self.source = (BACKEND / "console_server.py").read_text(encoding="utf-8")

    def test_console_passes_the_probe_budget_and_no_speed_probe(self):
        self.assertNotIn("speed-probe", self.source)
        self.assertIn('"--budget-secs"', self.source)
        self.assertIn("PROBE_BUDGET_SECS", self.source)

    def test_console_budget_stays_below_the_round_timeout(self):
        namespace = {}
        for line in self.source.splitlines():
            match = __import__("re").match(r"(SEARCH_TIMEOUT|PROBE_BUDGET_SECS) = (\d+)", line)
            if match:
                namespace[match.group(1)] = int(match.group(2))
        self.assertIn("SEARCH_TIMEOUT", namespace)
        self.assertIn("PROBE_BUDGET_SECS", namespace)
        self.assertLess(namespace["PROBE_BUDGET_SECS"], namespace["SEARCH_TIMEOUT"])

    def test_exit_code_mapping_tokens_are_unchanged(self):
        """0/4/5/7/8 的语义由这些 token 与 _search_status 决定，不能被改动带走。

        `UNSUPPORTED_THUNDER_DIRECT_LINK` 已从这份清单里去掉：它的唯一生产者是
        `probe_magnets --magnet`（连同 rc=3 一起删除），控制台里再留这条映射就是死代码，
        而它映射的正是本用例没列的 rc=3。解析不出 info hash 的行现在由
        `CANDIDATE_LINES_SKIPPED` 报数，不再静默（复核报告 F6）。
        """
        for token in (
            "SWARM_DEAD", "METADATA_FAILED", "NO_ELIGIBLE_RESOURCE",
            "NO_CANDIDATES", "BEST_BELOW_MIN_SCORE", "BEST_DEAD_SWARM",
        ):
            self.assertIn(token, self.source, token)
        self.assertIn('return "best_dead_swarm"', self.source)
        self.assertNotIn("UNSUPPORTED_THUNDER_DIRECT_LINK", self.source)

    def test_probe_still_exposes_the_same_exit_codes(self):
        probe_source = (BACKEND / "probe_magnets.py").read_text(encoding="utf-8")
        for code in ("return 0", "return 4", "return 5", "return 7", "return 8"):
            self.assertIn(code, probe_source, code)


# ---------------------------------------------------------------------------
# t14 修复轮的守卫：N5 夹具离线可复现 / N7 metadata_at 单点写入定义 /
# N1 PROBE_PARTIAL 有消费者 / N2 本轮没产出时不许拿上一轮快照冒充。
# 全部离线：网络入口打桩成「一调用就红」，不读不写 backend/state/。
# ---------------------------------------------------------------------------

FIXTURE_POOL = FIXTURES / "pool.json"
FIXTURE_POOL_NOTE = "_fixture_note"

USABLE_METADATA = {
    "name": "Fixture.Movie.Alpha.2024.1080p.WEB-DL.x264-TEST.mkv",
    "size_gb": 7.5,
    "nfiles": 1,
    "videos": [
        {
            "index": 0,
            "path": "Fixture.Movie.Alpha.2024.1080p.WEB-DL.x264-TEST.mkv",
            "name": "Fixture.Movie.Alpha.2024.1080p.WEB-DL.x264-TEST.mkv",
            "size": 8053063680,
        }
    ],
    "main_video_gb": 7.5,
    "main_video_ext": ".mkv",
    "junk_files": 0,
}
UNUSABLE_METADATA = {
    "name": "Nice.Name.But.No.Usable.Video.2024",
    "size_gb": 0.0,
    "nfiles": 3,
    "videos": [],
    "main_video_gb": 0.0,
    "main_video_ext": "",
    "junk_files": 2,
}


def console_module():
    """懒加载 console_server：只在需要控制台语义的用例里导入。

    导入它只建立常量与锁（服务在 `main()` 里才起），但它是整个控制台栈的入口，
    所以仍然推迟到用例内部，别拖累只想跑 probe 的部分。
    """
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import console_server
    return console_server


class FixtureOfflineByConstructionTests(ProbeCase):
    """N5：记录的命令不许依赖「手工把夹具时间戳刷成 now」。"""

    def test_fixture_pool_timestamps_are_fresh_sentinels(self):
        payload = json.loads(FIXTURE_POOL.read_text(encoding="utf-8"))
        self.assertIn(FIXTURE_POOL_NOTE, payload, "夹具缺少说明它为何永远新鲜的注记")
        horizon = time.time() + 365 * 24 * 3600
        for info_hash, entry in payload["entries"].items():
            for field in ("metadata_at", "health_at"):
                self.assertGreater(
                    float(entry[field]), horizon,
                    f"{info_hash} 的 {field} 不是远期哨兵值：直接跑夹具就会联网（t10/N5）",
                )

    def test_recorded_dry_run_command_is_offline_with_the_fixture_as_is(self):
        """夹具原样拷进临时目录就跑（不重写任何字段）→ 零联网、退出码 0。

        这就是 t9 记录的那条命令。以前它能让 rc=0 只是因为有人手工把 /tmp 池的
        时间戳刷成了 now；现在的结论由夹具本身保证。
        """
        pool_path = os.path.join(self.tmp, "pool-as-is.json")
        shutil.copyfile(FIXTURE_POOL, pool_path)
        patchers = offline_network_guard(self)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        rc, out_path, _ = self.run_probe(pool_path)  # 不传 --no-live-check

        self.assertEqual(rc, 0)
        probe = self.read_json(out_path)
        self.assertTrue(probe["ranked"], "夹具应有可交付候选")
        self.assertFalse(probe["partial"])


class MetadataStampWriteRuleTests(ProbeCase):
    """N7：`metadata_at` 只在「清单里确有可用视频」时才写，全仓只此一条定义。"""

    def test_enrich_writes_the_stamp_only_next_to_a_usable_file_list(self):
        cases = (("usable", USABLE_METADATA, True), ("unusable", UNUSABLE_METADATA, False))
        for label, metadata, expect_stamp in cases:
            with self.subTest(case=label):
                pool = {}
                with mock.patch.object(pm, "fetch_metadata", return_value=dict(metadata)):
                    pm.enrich("7" * 40, 25, 7200, pool, self.now, False)
                entry = pool["7" * 40]
                self.assertEqual(
                    "metadata_at" in entry, expect_stamp,
                    "没有可用视频的清单被盖了缓存戳 → 下一轮会以「已核实」命中（t10/F1 写入侧）",
                )
                # 其余字段照写：诊断信息不丢
                self.assertEqual(entry["name"], metadata["name"])
                self.assertEqual(entry["nfiles"], metadata["nfiles"])
                # 缓存是否命中与盖章规则一致
                cached = pm.pool_metadata(pool, "7" * 40, self.now)
                self.assertEqual(cached is None, not expect_stamp)

    def test_verify_live_keeps_the_old_stamp_when_the_list_has_no_usable_video(self):
        for label, metadata, expect_refresh in (
            ("usable", USABLE_METADATA, True),
            ("unusable", UNUSABLE_METADATA, False),
        ):
            with self.subTest(case=label):
                info_hash = "8" * 40
                old_stamp = self.now - 86400
                pool = {info_hash: {"name": "x", "metadata_at": old_stamp}}
                ranked = [{"ih": info_hash, "kind": "magnet", "name": "x", "external_peers": 50}]
                args = mock.Mock(live_check_top=3, live_check_ttl=3600, timeout=25)
                with mock.patch.object(pm, "fetch_metadata", return_value=dict(metadata)):
                    pm.verify_live(ranked, pool, self.now, args, round_started_at=self.now)
                entry = pool[info_hash]
                # 可达性证明照写（那是 probe_ok_at 的事），
                self.assertGreaterEqual(entry["probe_ok_at"], self.now)
                self.assertTrue(ranked[0]["metadata_verified"])
                self.assertEqual(
                    entry["metadata_at"] > old_stamp, expect_refresh,
                    "probe_ok_at 与 metadata_at 又被混成一个含义了（t10/N7）",
                )

    def test_metadata_at_definition_lives_in_one_place(self):
        """写入侧只剩 probe 一处规则：源码里只有一个 metadata_has_usable_video 判定。"""
        source = (BACKEND / "probe_magnets.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("def metadata_has_usable_video"), 1)
        self.assertNotIn('metadata_at=now, **metadata', source)
        self.assertNotIn("probe_ok_at=now, metadata_at=now", source)


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

    def ranked_speed(self, pool_path):
        """跑一次真实 probe 路径，取那个候选的 (speed 子分, breakdown.speed)。"""
        calls = []
        patchers = offline_network_guard(self)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        rc, out_path, _ = self.run_probe(pool_path, ["--no-live-check"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [], "离线用例里出现了网络调用")
        probe = self.read_json(out_path)
        rows = [row for row in probe["ranked"] if row["ih"] == self.NO_ANNOUNCE_HASH]
        self.assertTrue(rows, "第三个候选被剔掉了，用例前提不成立")
        return rows[0]["dimensions"]["speed"], rows[0]["breakdown"]["speed"]

    def test_missing_external_seeders_behaves_exactly_like_no_signal(self):
        """缺字段 = 与旧行为逐位相同：没有 announce、没有外部快照 → 中性 0.40。"""
        speed, parts = self.ranked_speed(self.pool_without_announce())
        self.assertEqual(parts["seed_source"], "none")
        self.assertEqual(parts["mode"], "unknown")
        self.assertAlmostEqual(speed, 40.0, delta=0.01)

    def test_external_seeders_reach_the_score_through_the_probe_path(self):
        """有外部快照（新鲜）→ speed 子分由外部先验决定，不再是恒 40.00/unknown。"""
        speed, parts = self.ranked_speed(
            self.pool_without_announce(external_seeders=5000, age_days=0.0)
        )
        self.assertEqual(parts["seed_source"], "external")
        self.assertEqual(parts["external_seeders"], 5000)
        self.assertEqual(parts["external_seeders_age_days"], 0.0)
        self.assertAlmostEqual(speed, 100.0, delta=0.01)

    def test_a_stale_external_snapshot_is_discounted_but_still_above_neutral(self):
        speed, parts = self.ranked_speed(
            self.pool_without_announce(external_seeders=5000, age_days=30.0)
        )
        self.assertEqual(parts["seed_source"], "external")
        self.assertAlmostEqual(parts["external_seeders_discount"], 0.54, delta=0.005)
        self.assertAlmostEqual(speed, 54.0, delta=0.01)
        self.assertGreater(speed, 40.0)

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
        speed, parts = self.ranked_speed(path)
        # Gamma 现在既有 announce 数据又有外部快照 → 先验必须来自 announce
        self.assertNotEqual(parts["seed_source"], "external")


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


if __name__ == "__main__":
    unittest.main()
