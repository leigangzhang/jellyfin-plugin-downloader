"""I4 集成契约测试：CLI 契约、离线干跑、退出码矩阵、第二通道接线与一致性。

覆盖本轮三份实现（I1 打分去偏 / I2 torrents-csv + L1 翻页 / I3 超时守卫 + live-check
复用 + tracker 列表）在**合起来**之后的行为：单个模块的测试各自在
`test_backend_scoring.py` / `test_backend_sources_*.py` / `test_backend_probe.py`
里，这里只钉“接口对上、接线没断、退出码没变”。

全部离线：网络入口（announce_health / tracker_health / fetch_metadata /
public_ip / search_api / torrents_csv_source.search）在用例里被替换成打桩或
「一被调用就失败」的守卫；不读不写 `backend/state/`，池文件一律放临时目录。
aria2c 测速入口已删除，所以这里（以及 probe 侧）没有它的桩。

I4 发现的三条集成缺陷（F1 外部候选被 metadata 关剔除 / F2 合并清掉已知文件清单 /
F3 严格按季时外部磁力不进候选文件）由 t11 修好后，已在 `KnownGapTests` 里转为**正常
断言**；各条 docstring 保留原始证据与修复口径。不要再给它们加回
`@unittest.expectedFailure`：Python 3.14 下 XPASS 会计入 unexpectedSuccesses 并把
套件判红（`wasSuccessful()` 要求该列表为空），等于让修好的东西假装还没修。
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

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
FIXTURES = ROOT / "tests" / "fixtures" / "jmd_probe"
SOURCES_FIXTURES = ROOT / "tests" / "fixtures" / "jmd_sources"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import candidate_score as cs  # noqa: E402
from probe import probe_magnets as pm  # noqa: E402
from search import search_pool as sp  # noqa: E402
import torrents_csv_source as tcs  # noqa: E402

# 三个 CLI 的退出码语义（不许被改动带走）。
EXIT_CODES = {"best": 0, "no_candidate": 4, "below_min_score": 5,
              "metadata_failed": 7, "swarm_dead": 8}


def offline_network_guard():
    """把 probe 的每个网络入口换成「被调用即失败」的桩，返回 (patchers, calls)。"""
    calls: list[str] = []

    def boom(name, test_case):
        def _inner(*args, **kwargs):
            calls.append(name)
            test_case.fail(f"离线用例里发生了真实网络调用：{name}{args!r}")
        return _inner

    return calls, boom


def diao_index_page(torrents=(), has_more=False):
    """一份 diao 索引响应的**响应体**（`search_api` 解析的就是这个形状）。"""
    return {"status": 200, "message": "",
            "data": {"keywords": [], "torrents": list(torrents), "has_more": has_more}}


class FakeIndexHttp:
    """`urllib.request.urlopen` 的替身：固定回一页索引响应，并数它被问过几次。

    必须打在 HTTP 边界上，而不是 `sp.search_api` 上：`fetch_query_pages` 把
    `search_api` 作为**默认参数**在定义时绑定（`def fetch_query_pages(..., search=search_api)`），
    所以 `mock.patch.object(sp, "search_api", ...)` 打不到 pager —— 用例会静默去问
    真实索引。t13 F-1 实测：本模块 17 条用例在 socket 钩子下触发 288 次外部网络事件，
    目标主机只有 `m.diao.im`（`172.67.209.90` / `104.21.69.146`）。

    写法与 `tests/_backend_sources_helpers.py` 的 `FakeHttp` 一致（替换
    `search_pool.urllib.request.urlopen`）。用例还必须断言 `requests` 非空，否则
    桩被绕过时没有人会发现——这正是 F-1 能潜伏这么久的原因。
    """

    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def __call__(self, request, *args, **kwargs):
        self.requests.append(getattr(request, "full_url", None) or str(request))
        return io.BytesIO(json.dumps(self.payload).encode("utf-8"))


class IntegrationCase(unittest.TestCase):
    # 第二通道的夹具 hash。定义在这里，因为 KnownGapTests 也用它（原来只有
    # SecondChannelWiringTests 有，导致 KnownGapTests 的三条用例跑都跑不起来）。
    EXTERNAL_HASH = "D" * 40

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jmd-i4-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = time.time()

    # ---- 工具 ---------------------------------------------------------------
    def fresh_pool(self, external_peers=200, entries=None, cold=False):
        """夹具 pool 的副本，TTL 字段写成「刚过期一点点」→ 不触发网络。

        `cold=True` 模拟冷池（metadata_at/health_at 归零），用于复现「必须联网抓
        metadata」的路径。
        """
        payload = json.loads((FIXTURES / "pool.json").read_text(encoding="utf-8"))
        if entries is not None:
            payload["entries"] = entries
        for entry in payload["entries"].values():
            entry["metadata_at"] = 0 if cold else self.now - 5
            entry["health_at"] = 0 if cold else self.now - 5
            entry["external_peers"] = external_peers
        return self._write_json("pool.json", payload)

    def _write_json(self, name, payload):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return path

    def run_probe(self, pool_path, extra_args=(), argv_extra=(), mocks=()):
        out_path = os.path.join(self.tmp, f"probe-{len(os.listdir(self.tmp))}.json")
        argv = [
            "probe_magnets.py", "fixture",
            "--kind", "movie",
            "--duration-minutes", "120",
            "--candidate-file", str(FIXTURES / "candidates.txt"),
            "--pool-file", pool_path,
            "--json-out", out_path,
            "--workers", "2",
            *extra_args, *argv_extra,
        ]
        stdout = io.StringIO()
        stack = [mock.patch.object(sys, "argv", argv)]
        stack.extend(mocks)
        for patcher in stack:
            patcher.start()
        try:
            with redirect_stdout(stdout):
                rc = pm.main()
        finally:
            for patcher in reversed(stack):
                patcher.stop()
        return rc, out_path, stdout.getvalue()

    def read_json(self, path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    ONLINE_PROBE_STUBS = {
        # 「记录并返回假数据」的桩：给那些**期望**探针真的去联网的用例用。
        # 用 boom()（记录后就 self.fail）装在这种用例上会自相矛盾——
        # 想要的行为一旦发生，用例自己先失败。
        "fetch_metadata": {
            "name": "External.Only.2024.1080p.WEB-DL.x264-TEST", "size_gb": 7.5,
            "nfiles": 1, "main_video_gb": 7.5, "main_video_ext": ".mkv",
            "junk_files": 0, "junk_bytes": 0,
            "videos": [{"index": 0, "path": "e.mkv", "name": "e.mkv",
                        "size": 8053063680}],
        },
        "announce_health": {"seeders": 151, "own_peers": 1, "external_peers": 150},
        "tracker_health": (150, 160),
        "public_ip": "203.0.113.7",
    }

    def probe_network_patchers(self, calls):
        def boom(name):
            def _inner(*args, **kwargs):
                calls.append(name)
                self.fail(f"离线用例里发生了真实网络调用：{name}{args!r}")
            return _inner

        def record(name, value):
            def _inner(*args, **kwargs):
                calls.append(name)
                return value
            return _inner

        if getattr(self, "RECORDS_PROBE_CALLS", False):
            return [
                mock.patch.object(pm, name, record(name, value))
                for name, value in self.ONLINE_PROBE_STUBS.items()
            ]
        return [
            mock.patch.object(pm, "announce_health", boom("announce_health")),
            mock.patch.object(pm, "tracker_health", boom("tracker_health")),
            mock.patch.object(pm, "fetch_metadata", boom("fetch_metadata")),
            mock.patch.object(pm, "public_ip", boom("public_ip")),
        ]


class CliContractTests(IntegrationCase):
    """三个 CLI 必须能执行、--help 退出码为 0。"""

    def _help(self, script, *extra):
        return subprocess.run(
            [sys.executable, str(BACKEND / script), *extra, "--help"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )

    def test_probe_magnets_help(self):
        done = self._help("probe/probe_magnets.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--budget-secs", done.stdout)

    def test_search_pool_help(self):
        done = self._help("search/search_pool.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        for flag in ("--page-budget-secs", "--page-workers",
                     "--no-new-pages", "--page-errors", "--collapse-releases"):
            self.assertIn(flag, done.stdout, flag)

    def test_candidate_score_help(self):
        """candidate_score 是库不是 CLI：`--help` 被忽略并正常退出（无 argparse）。"""
        done = self._help("candidate_score.py")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_no_third_party_imports_in_the_new_module(self):
        """I2 声明的「零第三方依赖」：新模块的顶层 import 只允许标准库。"""
        source = (BACKEND / "torrents_csv_source.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if line.startswith(("import ", "from ")):
                module = line.split()[1].split(".")[0]
                self.assertIn(
                    module,
                    {"argparse", "csv", "datetime", "json", "os", "time",
                     "urllib", "typing", "math", "re"},
                    f"torrents_csv_source 引入了非标准库依赖：{line}",
                )


class OfflineDryRunTests(IntegrationCase):
    """验收要求的离线干跑：fixtures 候选 + /tmp 池，全程离线。"""

    def _dry_run(self, mock_network=True):
        pool_path = self.fresh_pool()
        args = ["--no-live-check"]
        calls = []
        mocks = self.probe_network_patchers(calls) if mock_network else []
        rc, out_path, stdout = self.run_probe(pool_path, args, mocks=mocks)
        return rc, out_path, stdout, calls

    def test_dry_run_is_fully_offline_and_ranks_the_fixture_candidates(self):
        rc, out_path, _, calls = self._dry_run()
        self.assertEqual(calls, [], "干跑必须完全离线")
        self.assertEqual(rc, 0)
        probe = self.read_json(out_path)
        self.assertTrue(probe["ranked"])
        self.assertIsNotNone(probe["best"])
        for row in probe["ranked"]:
            self.assertIn("dimensions", row)
            self.assertIn("breakdown", row)
        # 总分 = Σ(权重 × 维度)：跨模块（candidate_score 的权重 × probe 的流水线）
            weights = probe["policy"]["weights"]
            expected = sum(row["dimensions"][name] * weights[name] for name in weights)
            self.assertAlmostEqual(row["score"], expected, delta=0.05)

    def test_dry_run_as_a_subprocess_lands_in_the_allowed_exit_set(self):
        pool_path = self.fresh_pool()
        out_path = os.path.join(self.tmp, "subprocess-probe.json")
        done = subprocess.run(
            [sys.executable, str(BACKEND / "probe" / "probe_magnets.py"), "fixture",
             "--kind", "movie", "--duration-minutes", "120",
             "--candidate-file", str(FIXTURES / "candidates.txt"),
             "--pool-file", pool_path, "--json-out", out_path,
             "--workers", "2", "--no-live-check"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertIn(done.returncode, {0, 4, 5}, done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:])
        probe = self.read_json(out_path)
        self.assertTrue(probe["ranked"] and probe["best"])
        self.assertIn("dimensions", probe["ranked"][0])
        self.assertIn("breakdown", probe["ranked"][0])

    def test_probe_json_exposes_the_new_budget_and_stage_fields(self):
        """I3 的输出契约：partial/timeout_stage/expired_stages/stage_seconds/completed。"""
        rc, out_path, _, _ = self._dry_run()
        self.assertEqual(rc, 0)
        probe = self.read_json(out_path)
        for key in ("partial", "timeout_stage", "expired_stages", "stage_seconds",
                    "completed", "live_check", "budget_secs"):
            self.assertIn(key, probe, key)
        self.assertFalse(probe["partial"])
        self.assertIn("health", probe["stage_seconds"])
        self.assertGreaterEqual(probe["completed"]["metadata"], 1)


class ExitCodeMatrixTests(IntegrationCase):
    """0/4/5/7/8 五个退出码在离线条件下逐一复现，语义未被集成带走。"""

    def _argv(self, extra):
        return [
            "--kind", "movie", "--duration-minutes", "120",
            "--candidate-file", str(FIXTURES / "candidates.txt"), *extra,
        ]

    def test_zero_best_result(self):
        rc, out_path, stdout = self.run_probe(self.fresh_pool(), ["--no-live-check"])
        self.assertEqual(rc, EXIT_CODES["best"])
        self.assertIn("BEST_RESULT_JSON", stdout)
        self.assertIsNotNone(self.read_json(out_path)["best"])

    def test_four_no_candidates(self):
        empty = os.path.join(self.tmp, "empty.txt")
        Path(empty).write_text("# no candidates\n", encoding="utf-8")
        out_path = os.path.join(self.tmp, "no-cand.json")
        argv = ["probe_magnets.py", "x", "--kind", "movie", "--candidate-file", empty,
                "--no-pool", "--json-out", out_path, "--workers", "2"]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(buf):
            rc = pm.main()
        self.assertEqual(rc, EXIT_CODES["no_candidate"])
        self.assertIn("NO_CANDIDATES", buf.getvalue())

    def test_five_below_min_score(self):
        rc, _, stdout = self.run_probe(
            self.fresh_pool(), ["--no-live-check", "--min-score", "101"]
        )
        self.assertEqual(rc, EXIT_CODES["below_min_score"])
        self.assertIn("BEST_BELOW_MIN_SCORE", stdout)

    def test_seven_metadata_failed_with_external_peers(self):
        """冷池 + 抓不到 metadata + announce 仍有外部 peer → 网络问题，退出码 7。"""
        health = {
            "tracker": "t", "seeders": 50, "leechers": 5, "peers": 50,
            "external_peers": 50, "own_peers": 0, "own_ips": [],
        }
        mocks = [
            mock.patch.object(pm, "announce_health", lambda *a, **k: dict(health)),
            mock.patch.object(pm, "fetch_metadata", lambda *a, **k: None),
            mock.patch.object(pm, "public_ip", lambda: ""),
        ]
        rc, _, stdout = self.run_probe(
            self.fresh_pool(cold=True), ["--no-live-check"], mocks=mocks
        )
        self.assertEqual(rc, EXIT_CODES["metadata_failed"])
        self.assertIn("METADATA_FAILED", stdout)

    def test_eight_best_is_dead_swarm(self):
        rc, _, stdout = self.run_probe(
            self.fresh_pool(external_peers=0), ["--no-live-check"]
        )
        self.assertEqual(rc, EXIT_CODES["swarm_dead"])
        self.assertIn("BEST_DEAD_SWARM", stdout)


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


class KnownGapTests(IntegrationCase):
    """I4 发现的三条集成缺陷 —— 已由 t11 修复，这里从 expectedFailure 转为正常断言。

    说明（t11 记录）：这三条用例此前**不具备验证能力**。它们原本挂在
    `expectedFailure` 下，而去掉该装饰器后三条会立刻以
    `AttributeError: 'KnownGapTests' object has no attribute 'EXTERNAL_HASH'` 报错
    （`EXTERNAL_HASH` 当时只定义在 `SecondChannelWiringTests`），从未跑到断言；
    另外 F1 那条用的 `probe_network_patchers` 桩是「记录后 self.fail」，与它自己的
    断言 `assertTrue(calls or probe_rc == 0)` 互斥 —— 修好缺陷（探针真的联网）必然
    触发 self.fail。所以「红」与它要钉的缺陷无关。修好后三条转为正常通过。
    """

    RECORDS_PROBE_CALLS = True

    def test_external_candidate_survives_the_probe_metadata_gate(self):
        """第二通道的候选应当能进排名（修复前：在 metadata 关被直接剔掉，且从不联网抓）。

        证据（本机实测，行号对应 search_pool.py sha1 9ec4379 / probe_magnets.py sha1 a5eb94d）：
          * search_pool.py:739-740 给**所有**候选盖 `metadata_at = now`，包括
            torrents-csv 条目；后者 `videos: []`、`main_video_gb: 0.0`；
          * search_pool.py:967-976 把这份候选写进池子；
          * probe_magnets.py:563-576 把「有 name + metadata_at 新鲜」当成有效 metadata，
            原样返回空 `videos`；probe_magnets.py:1331 的 structural_reject
            （candidate_score.py:383）随即给出
            "no readable video file >=100MB in torrent metadata"。
        建议修复（二选一）：
          (a) search_pool 只给**带文件清单**的候选盖 metadata_at，外部通道的条目留空
              （probe 于是会真的去 swarm 抓 metadata）；
          (b) probe_magnets.pool_metadata 把 `videos` 为空的缓存视为未命中。
        """
        rc, pool_path, cand_out, _, _ = SecondChannelWiringTests._run_search_pool(
            self, {"version": 1, "query": "x", "updated_at": "x", "entries": {}},
            [SecondChannelWiringTests._external_entry(self)],
        )
        self.assertEqual(rc, 0)
        # 用 search_pool 刚写出的池子 + 它写出的候选文件跑 probe（网络打桩）
        calls = []
        mocks = self.probe_network_patchers(calls)
        out_path = os.path.join(self.tmp, "external-probe.json")
        argv = ["probe_magnets.py", "x", "--kind", "movie", "--duration-minutes", "120",
                "--candidate-file", cand_out, "--pool-file", pool_path,
                "--json-out", out_path, "--workers", "2", "--no-live-check"]
        for patcher in mocks:
            patcher.start()
        try:
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                probe_rc = pm.main()
        finally:
            for patcher in reversed(mocks):
                patcher.stop()
        # 真断言（t13 F-2：原来那句 `assertTrue(calls or probe_rc == 0)` 永真——
        # RECORDS_PROBE_CALLS 的桩会把 calls 填满，所以它钉不住任何东西）。
        # 两件必须同时成立的事：外部候选**真的进了排名**，而且探针**真的为它抓过
        # metadata**（修复前：空 videos 的池条目被当成有效缓存 → 不抓、直接被剔）。
        self.assertTrue(
            os.path.exists(out_path),
            "探针没有写出结果 JSON：外部候选在 metadata 关被整段剔掉了（F1 复现）",
        )
        probe = self.read_json(out_path)
        ranked = [item.get("ih") for item in probe.get("ranked") or []]
        self.assertIn(
            self.EXTERNAL_HASH, ranked,
            "第二通道候选没有进排名（metadata 关把它剔掉了）",
        )
        self.assertIn(
            "fetch_metadata", calls,
            "探针没有为外部候选抓 metadata（空文件清单被当成了有效缓存）",
        )
        self.assertEqual(probe_rc, 0)

    def test_external_merge_does_not_erase_a_known_file_list(self):
        """池里已知的文件清单不该被第二通道的空清单覆盖。

        原始证据（I4 实测，本用例复现）：池中某 hash 已有 `videos`/`main_video_gb`，
        当时的 `entry.update(...)` 把候选的空 `videos: []`、`main_video_gb: 0.0`、
        `nfiles: 0` 盖上去，并把 `metadata_at` 刷成 now → 该 hash 在 METADATA_TTL
        （7 天）内再也不会被 probe 抓 metadata，直接判「没有可读视频」。
        t11 修法：合并改走 `merge_pool_entry`——空值不覆盖非空值、`metadata_at` 不被
        合并刷新，且只知名字的候选只能补 swarm/来源字段。
        """
        known_hash = "C" * 40
        payload = {"version": 1, "query": "x", "updated_at": "x", "entries": {
            known_hash: {
                "name": "Previously.Probed.2023.1080p.WEB-DL.x264-GRP.mkv",
                "videos": [{"index": 0, "path": "p.mkv", "name": "p.mkv",
                            "size": 8053063680}],
                "main_video_gb": 7.5, "size_gb": 7.5, "nfiles": 1,
                "main_video_ext": ".mkv", "junk_files": 0, "junk_bytes": 0,
                "metadata_at": self.now - 86400, "health_at": self.now - 86400,
                "seeds": 30, "peers": 40, "external_peers": 40,
                "health_method": "announce",
            }}}
        entry = SecondChannelWiringTests._external_entry(self, info_hash=known_hash)
        rc, pool_path, _, _, _ = SecondChannelWiringTests._run_search_pool(
            self, payload, [entry]
        )
        self.assertEqual(rc, 0)
        after = self.read_json(pool_path)["entries"][known_hash]
        self.assertEqual(after.get("main_video_gb"), 7.5,
                         "已知的 main_video_gb 被第二通道的空清单覆盖")
        self.assertTrue(after.get("videos"), "已知的文件清单被空清单覆盖")

    def test_strict_season_search_still_writes_external_magnets(self):
        """严格按季（控制台默认）时，第二通道的磁力不该被候选文件丢掉。

        原始证据（I4 实测）：`write_candidates_file` 只写 `hit_query` 命中某个 L1
        report query 的候选；严格模式下 report query 只有「片名 第N季 / 片名 S01」
        这类整包词，而第二通道用的是裸片名/别名 → 它的候选**一条都不写进候选文件**，
        probe 根本看不到（probe 只用 --candidate-file，不把池子当候选来源）。
        t11 修法：未被任何 report 命中的 hit_query 以 `# extra-source query` 分组补写，
        磁力行数与候选数一致。
        """
        pool_payload = {"version": 1, "query": "x", "updated_at": "x", "entries": {}}
        entry = SecondChannelWiringTests._external_entry(self)
        pool_path = self._write_json("strict-pool.json", pool_payload)
        cand_out = os.path.join(self.tmp, "strict.magnets")
        argv = ["search_pool.py", "External Only", "--strict-match",
                "--collection-hint", "第1季", "--pool-file", pool_path,
                "--candidates-out", cand_out,
                "--json-out", os.path.join(self.tmp, "strict-search.json")]
        # 严格模式下，ASCII 片名 + 中文整包词会被 `build_query_plan` 跳过（中英不混搭），
        # 所以这条用例的 L1 阶梯本来就是空的 —— 桩没被问过是预期行为，不是被绕过。
        # 用阶梯本身来判「该不该有请求」，比无条件要求 requests 非空更准确。
        strict_plan = sp.build_query_plan(
            "External Only", collection_hints=["第1季"], strict_match=True
        )
        empty_page = diao_index_page()
        index_http = FakeIndexHttp(empty_page)
        result = {"status": 200, "message": "", "query": "", "elapsed_secs": 0.1,
                  "entries": [entry]}
        with mock.patch.object(sp.urllib.request, "urlopen", index_http), \
             mock.patch.object(tcs, "search", side_effect=lambda query, **kw: dict(result)):
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                rc = sp.main()
        self.assertTrue(
            index_http.requests or not strict_plan,
            "L1 阶梯非空却没问过索引桩：桩没打在真正的 HTTP 边界上（t13 F-1 会复现）",
        )
        self.assertEqual(rc, 0)
        magnets = [line for line in Path(cand_out).read_text(encoding="utf-8").splitlines()
                   if line and not line.startswith("#")]
        self.assertIn(f"magnet:?xt=urn:btih:{self.EXTERNAL_HASH}", magnets)


if __name__ == "__main__":
    unittest.main()
