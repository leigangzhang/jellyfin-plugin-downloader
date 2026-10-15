"""路由级：`GET /api/trending` 与 `POST /api/trending/watch` 的形状与降级。

用假 opener 驱动真实 `ConsoleHandler` 的方法（走 `do_GET` / `do_POST` 的分派，
不是直接调 `_get_trending`）：面板真正会命中的那条路径是分派表，绕过它就等于
没测接线。两条边界被替换：HTTP（`trending_source.default_opener`）与数据根
（`temp_data_root`），其余（解析、缓存、关注清单、信封）全是真的。

无 key 那条用例同时是 A1 的后端半边：返回引导文案，且假 opener 的调用计数为 0。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.backend_trending import (
    FakeTMDb,
    HandlerStub,
    temp_data_root,
    trending_source,
    trending_watchlist,
)

LANG = "zh-CN"
IDS = list(trending_source.LIST_ORDER)
EXPECTED_LABELS = {
    "trending_movie_week": "本周热门电影",
    "trending_tv_week": "本周热门剧集",
    "popular_movie": "流行电影",
    "popular_tv": "流行剧集",
}
WATCHED = {"tmdb_id": 550, "title": "Fight Club", "year": 1999, "kind": "movie"}


class TrendingRouteTests(unittest.TestCase):
    def test_get_without_api_key_returns_guidance_and_never_touches_tmdb(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root(), mock.patch.dict(
            os.environ, {"JMD_TMDB_API_KEY": ""}
        ), mock.patch.object(trending_source, "default_opener", fake.opener):
            stub = HandlerStub(path="/api/trending")
            stub.do_GET()

        self.assertEqual(stub.status, 200)
        self.assertEqual(stub.payload["lists"], [])
        self.assertIs(stub.payload["stale"], False)
        self.assertIn("TMDb API Key", stub.payload["guidance"])
        self.assertIn("控制台", stub.payload["guidance"])
        self.assertEqual(stub.payload["watched"], [])
        self.assertEqual(fake.calls, [])

    def test_missing_env_var_is_the_same_as_an_empty_one(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root(), mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
            trending_source, "default_opener", fake.opener
        ):
            os.environ.pop("JMD_TMDB_API_KEY", None)
            stub = HandlerStub(path="/api/trending")
            stub.do_GET()

        self.assertEqual(stub.status, 200)
        self.assertIn("TMDb API Key", stub.payload["guidance"])
        self.assertEqual(fake.calls, [])

    def test_get_with_key_returns_at_least_two_lists_of_ten_items_and_watched(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root(), mock.patch.dict(
            os.environ, {"JMD_TMDB_API_KEY": "ROUTE-KEY"}
        ), mock.patch.object(trending_source, "default_opener", fake.opener):
            trending_watchlist.add_watch(WATCHED, now=1700000000)
            stub = HandlerStub(path="/api/trending")
            stub.do_GET()

        self.assertEqual(stub.status, 200)
        payload = stub.payload
        self.assertIsNone(payload["guidance"])
        self.assertIs(payload["stale"], False)
        self.assertGreaterEqual(len(payload["lists"]), 2)
        self.assertEqual([entry["id"] for entry in payload["lists"]], IDS)
        for entry in payload["lists"]:
            self.assertEqual(entry["label"], EXPECTED_LABELS[entry["id"]])
            self.assertGreaterEqual(len(entry["items"]), 10, entry["id"])
        self.assertEqual(payload["watched"], [550])
        # 每个榜单一次请求，都带上了 key 与语言
        self.assertEqual(len(fake.calls), len(IDS))
        for url in fake.urls:
            self.assertIn("api_key=ROUTE-KEY", url)
            self.assertIn(f"language={LANG}", url)

    def test_unknown_list_ids_are_ignored_and_language_is_forwarded(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root(), mock.patch.dict(
            os.environ, {"JMD_TMDB_API_KEY": "ROUTE-KEY"}
        ), mock.patch.object(trending_source, "default_opener", fake.opener):
            stub = HandlerStub(
                path="/api/trending?language=en-US&lists=popular_movie,no_such_list"
            )
            stub.do_GET()

        self.assertEqual([entry["id"] for entry in stub.payload["lists"]], ["popular_movie"])
        self.assertEqual(stub.payload["lists"][0]["label"], "流行电影")
        self.assertEqual(fake.paths, ["/3/movie/popular"])
        self.assertIn("language=en-US", fake.urls[0])

    def test_second_get_inside_the_ttl_does_not_call_tmdb_again(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root(), mock.patch.dict(
            os.environ, {"JMD_TMDB_API_KEY": "ROUTE-KEY"}
        ), mock.patch.object(trending_source, "default_opener", fake.opener):
            first = HandlerStub(path="/api/trending")
            first.do_GET()
            calls_after_first = len(fake.calls)
            second = HandlerStub(path="/api/trending")
            second.do_GET()

        self.assertEqual(calls_after_first, len(IDS))
        self.assertEqual(len(fake.calls), calls_after_first)
        self.assertEqual(second.payload, first.payload)

    def test_unknown_path_is_still_a_404(self):
        stub = HandlerStub(path="/api/trending/unknown")
        stub.do_GET()
        self.assertEqual(stub.status, 404)

    def test_post_watch_requires_tmdb_id(self):
        with temp_data_root():
            for body in ({}, {"tmdb_id": None}, {"tmdb_id": ""}, {"tmdb_id": "   "}):
                stub = HandlerStub(path="/api/trending/watch", body=body)
                stub.do_POST()
                self.assertEqual(stub.status, 400, body)
                self.assertEqual(stub.payload, {"error": "tmdb_id is required"}, body)

    def test_post_watch_rejects_a_non_numeric_id(self):
        with temp_data_root():
            stub = HandlerStub(path="/api/trending/watch", body={"tmdb_id": "abc"})
            stub.do_POST()
            self.assertEqual(stub.status, 400)
            self.assertEqual(stub.payload, {"error": "invalid tmdb_id"})

    def test_post_watch_is_idempotent(self):
        with temp_data_root():
            body = dict(WATCHED)
            first = HandlerStub(path="/api/trending/watch", body=body)
            first.do_POST()
            self.assertEqual(first.status, 200)
            self.assertEqual(first.payload, {"ok": True, "exists": False, "written": True})

            again = HandlerStub(path="/api/trending/watch", body={"tmdb_id": "550"})
            again.do_POST()
            self.assertEqual(again.status, 200)
            self.assertEqual(again.payload, {"ok": True, "exists": True, "written": False})
            self.assertEqual(trending_watchlist.watchlist_ids(), [550])

    def test_watched_ids_are_returned_even_without_an_api_key(self):
        with temp_data_root(), mock.patch.dict(os.environ, {"JMD_TMDB_API_KEY": ""}):
            trending_watchlist.add_watch(WATCHED, now=1700000000)
            stub = HandlerStub(path="/api/trending")
            stub.do_GET()

        self.assertEqual(stub.payload["watched"], [550])
        self.assertIn("TMDb API Key", stub.payload["guidance"])


if __name__ == "__main__":
    unittest.main()
