"""I4 三条集成缺陷（F1/F2/F3）的回归断言 —— 已由 t11 修复，这里从 expectedFailure 转正常断言。

这三条用例复用 `SecondChannelWiringTests` 的两个私有驱动器（`_external_entry` /
`_run_search_pool`），所以本模块按**模块对象**引用兄弟模块
（`second_channel.SecondChannelWiringTests...`）：`from ... import SecondChannelWiringTests`
会把那个 TestCase 类塞进本模块命名空间，`unittest discover` 于是把它再收集一遍，`Ran` 翻倍。
用例体、断言、docstring 与拆分前逐字相同，只把类名限定到模块。
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.backend_probe import test_backend_second_channel as second_channel
from tests.backend_probe._backend_integration_helpers import (
    FakeIndexHttp,
    IntegrationCase,
    diao_index_page,
    pm,
    sp,
    tcs,
)


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
        rc, pool_path, cand_out, _, _ = second_channel.SecondChannelWiringTests._run_search_pool(
            self, {"version": 1, "query": "x", "updated_at": "x", "entries": {}},
            [second_channel.SecondChannelWiringTests._external_entry(self)],
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
        entry = second_channel.SecondChannelWiringTests._external_entry(self, info_hash=known_hash)
        rc, pool_path, _, _, _ = second_channel.SecondChannelWiringTests._run_search_pool(
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
        entry = second_channel.SecondChannelWiringTests._external_entry(self)
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
