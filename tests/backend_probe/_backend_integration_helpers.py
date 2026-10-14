"""I4 集成契约测试的共享件：常量、离线守卫、边界桩与基类（本文件不放用例）。

拆分前这些共享件与用例同住 `tests/backend_probe/test_backend_integration.py`
（32467 B / 643 行，已按主题拆成同目录兄弟模块）。`_` 前缀不被
`unittest discover` 收集，所以本文件**不许出现 TestCase 子类**——出现即被算进用例数，
而拆分前后都必须是 `Ran 315`。用例的住处：

  * `test_backend_cli_contract.py` —— CliContractTests / OfflineDryRunTests
  * `test_backend_exit_matrix.py` —— ExitCodeMatrixTests
  * `test_backend_second_channel.py` —— SecondChannelWiringTests
  * `test_backend_known_gap.py` —— KnownGapTests

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

# 后端模块在这里 import 一次（先做 BACKEND 进 sys.path 的路径手术）。本文件自身只用
# `pm`；`cs` / `sp` / `tcs` 是给兄弟测试模块的 re-export。
import candidate_score as cs  # noqa: E402,F401 -- re-exported: 兄弟测试模块从这里取用
from probe import probe_magnets as pm  # noqa: E402
from search import search_pool as sp  # noqa: E402,F401 -- re-exported
import torrents_csv_source as tcs  # noqa: E402,F401 -- re-exported

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
