"""夹具离线可复现与 metadata_at 单点写入的守卫。

拆自 `tests/backend_probe/test_backend_probe.py`（按主题），共享件在
`tests/backend_probe/_backend_probe_helpers.py`。"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from unittest import mock

from tests.backend_probe._backend_probe_helpers import (
    BACKEND,
    FIXTURES,
    FIXTURE_POOL,
    FIXTURE_POOL_NOTE,
    ProbeCase,
    UNUSABLE_METADATA,
    USABLE_METADATA,
    console_module,
    offline_network_guard,
    pm,
)

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
        # 定义搬到了 probe_pool.py（池缓存的读侧与 metadata 的写侧共用它），
        # 但「全仓只有一处定义」这条不变；probe_magnets 只做重绑定。
        source = (BACKEND / "probe" / "probe_pool.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("def metadata_has_usable_video"), 1)
        probe_source = "\n".join(
            (BACKEND / name).read_text(encoding="utf-8")
            for name in ("probe/probe_magnets.py", "probe/probe_round.py", "probe/probe_metadata.py")
        )
        self.assertEqual(probe_source.count("def metadata_has_usable_video"), 0)
        self.assertNotIn('metadata_at=now, **metadata', source + probe_source)
        self.assertNotIn("probe_ok_at=now, metadata_at=now", source + probe_source)

