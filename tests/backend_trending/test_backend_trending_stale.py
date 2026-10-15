"""A4：TMDb 不可达时回退上次缓存并置 `stale=True`；没有缓存时给空榜单 + 错误说明。

两条路径都从真实链路走：先成功写缓存 → 再让 opener 抛 `OSError` → 看返回的
是不是上一轮那份数据。最后一条用例把同一条降级路径从**路由**再走一遍
（`GET /api/trending` → 缓存 → 取源），证明面板拿到的是 stale 而不是 500。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.backend_trending import (
    FakeTMDb,
    HandlerStub,
    temp_data_root,
    trending_cache,
    trending_source,
)

LANG = "zh-CN"
IDS = list(trending_source.LIST_ORDER)
TTL = trending_cache.CACHE_TTL_SECS


class TrendingStaleTests(unittest.TestCase):
    def test_unreachable_tmdb_returns_the_last_cache_marked_stale(self):
        good = FakeTMDb(per_list=20)
        broken = FakeTMDb(error=OSError("network is unreachable"))
        with temp_data_root():
            warm = trending_cache.load_lists(LANG, IDS, "KEY", opener=good.opener, now=5000.0)
            self.assertFalse(warm["stale"])

            result = trending_cache.load_lists(
                LANG, IDS, "KEY", opener=broken.opener, now=5000.0 + TTL + 1
            )
            self.assertTrue(result["stale"])
            self.assertEqual(result["lists"], warm["lists"])
            self.assertGreaterEqual(len(result["lists"][0]["items"]), 10)
            self.assertTrue(result["error"])
            self.assertIn("network is unreachable", result["error"])
            # 失败的那一轮确实打过 TMDb（证明「stale」来自回退而不是没试）
            self.assertEqual(len(broken.calls), len(IDS))

    def test_no_cache_returns_empty_lists_with_an_error_explanation(self):
        broken = FakeTMDb(error=OSError("timed out"))
        with temp_data_root():
            result = trending_cache.load_lists(LANG, IDS, "KEY", opener=broken.opener, now=5000.0)
            self.assertTrue(result["stale"])
            self.assertTrue(result["error"])
            self.assertIn("timed out", result["error"])
            self.assertEqual([entry["id"] for entry in result["lists"]], IDS)
            for entry in result["lists"]:
                self.assertEqual(entry["items"], [], entry["id"])
            # 空榜单没被写进缓存：下一轮还要再试
            self.assertEqual(trending_cache.read_cache(), {})

    def test_route_returns_stale_lists_when_tmdb_is_unreachable(self):
        good = FakeTMDb(per_list=20)
        broken = FakeTMDb(error=OSError("network is unreachable"))
        with temp_data_root(), mock.patch.dict(os.environ, {"JMD_TMDB_API_KEY": "ROUTE-KEY"}):
            # 先用真链路暖一份缓存（fetched_at=1.0，对真实时钟来说早就过期）
            warm = trending_cache.load_lists(LANG, IDS, "ROUTE-KEY", opener=good.opener, now=1.0)
            self.assertFalse(warm["stale"])

            with mock.patch.object(trending_source, "default_opener", broken.opener):
                stub = HandlerStub(path=f"/api/trending?lists={','.join(IDS)}")
                stub.do_GET()

            self.assertEqual(stub.status, 200)
            self.assertTrue(stub.payload["stale"])
            self.assertIsNone(stub.payload["guidance"])
            self.assertEqual(
                [entry["id"] for entry in stub.payload["lists"]], IDS
            )
            self.assertGreaterEqual(len(stub.payload["lists"][0]["items"]), 10)


if __name__ == "__main__":
    unittest.main()
