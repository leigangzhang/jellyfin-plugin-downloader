"""取源层：四个榜单归一成统一条目，任何失败都折成结构化结果而不是抛异常。

替身只换掉进程外的 HTTP（`FakeTMDb`），归一化、URL 拼装、错误分类全是真的。
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error

from tests.backend_trending import (
    FakeTMDb,
    FakeResponse,
    ITEM_KEYS,
    tmdb_payload,
    trending_source,
)

EXPECTED_LABELS = {
    "trending_movie_week": ("本周热门电影", "movie"),
    "trending_tv_week": ("本周热门剧集", "series"),
    "popular_movie": ("流行电影", "movie"),
    "popular_tv": ("流行剧集", "series"),
}


def _identity_opener(body, status=200):
    def opener(request, timeout=None):
        return FakeResponse(body, status=status)

    return opener


class TrendingSourceTests(unittest.TestCase):
    def test_all_four_lists_carry_at_least_ten_items_in_the_unified_shape(self):
        fake = FakeTMDb(per_list=20)
        lists = trending_source.fetch_lists(
            trending_source.LIST_ORDER, "KEY", opener=fake.opener
        )

        self.assertEqual(
            [entry["id"] for entry in lists], list(trending_source.LIST_ORDER)
        )
        self.assertEqual(len(lists), 4)
        for entry in lists:
            label, kind = EXPECTED_LABELS[entry["id"]]
            self.assertEqual(entry["status"], 200, entry)
            self.assertEqual(entry["error"], "", entry)
            self.assertEqual(entry["label"], label)
            self.assertEqual(entry["kind"], kind)
            self.assertGreaterEqual(len(entry["items"]), 10, entry["id"])
            for item in entry["items"]:
                self.assertEqual(set(item), ITEM_KEYS)
                self.assertIsInstance(item["tmdb_id"], int)
                self.assertIsInstance(item["year"], int)
                self.assertIsInstance(item["vote_average"], float)
                self.assertTrue(item["title"], item)
                self.assertTrue(item["original_title"], item)
                self.assertTrue(item["poster_path"].endswith(".jpg"), item)
                self.assertEqual(item["kind"], kind)

    def test_kind_follows_the_list_not_the_record(self):
        # 同一部作品出现在两个榜里时，类型跟着榜单走（trending_tv_week 是 series）
        fake = FakeTMDb(per_list=12)
        entries = {
            entry["id"]: entry
            for entry in trending_source.fetch_lists(
                ("popular_movie", "popular_tv"), "KEY", opener=fake.opener
            )
        }
        self.assertEqual(entries["popular_movie"]["items"][0]["kind"], "movie")
        self.assertEqual(entries["popular_tv"]["items"][0]["kind"], "series")
        self.assertTrue(entries["popular_movie"]["items"][0]["title"].startswith("电影"))
        self.assertTrue(entries["popular_tv"]["items"][0]["title"].startswith("剧集"))

    def test_missing_or_malformed_dates_yield_a_null_year(self):
        payload = {
            "page": 1,
            "results": [
                {"id": 1, "title": "无日期"},
                {"id": 2, "title": "空日期", "release_date": ""},
                {"id": 3, "title": "怪日期", "release_date": "not-a-date"},
                {"id": 4, "title": "好日期", "release_date": "1999-10-15"},
            ],
        }
        entry = trending_source.fetch_list(
            "popular_movie",
            "KEY",
            opener=_identity_opener(json.dumps(payload).encode("utf-8")),
        )
        self.assertEqual([item["year"] for item in entry["items"]], [None, None, None, 1999])

    def test_records_that_are_not_objects_are_dropped(self):
        payload = {"page": 1, "results": [{"id": 7, "title": "好"}, "坏记录", None, 12]}
        entry = trending_source.fetch_list(
            "popular_movie",
            "KEY",
            opener=_identity_opener(json.dumps(payload).encode("utf-8")),
        )
        self.assertEqual([item["tmdb_id"] for item in entry["items"]], [7])

    def test_request_carries_endpoint_key_language_page_and_user_agent(self):
        fake = FakeTMDb(per_list=10)
        trending_source.fetch_list(
            "trending_tv_week", "MY-KEY", language="en-US", page=2, opener=fake.opener
        )
        self.assertEqual(fake.paths, ["/3/trending/tv/week"])
        url = fake.urls[0]
        self.assertIn("api_key=MY-KEY", url)
        self.assertIn("language=en-US", url)
        self.assertIn("page=2", url)
        self.assertEqual(fake.calls[0]["ua"], trending_source.USER_AGENT)
        self.assertEqual(fake.calls[0]["timeout"], trending_source.DEFAULT_TIMEOUT)

    def test_build_url_has_no_machine_dependent_part(self):
        url = trending_source.build_url("popular_tv", "K", language="zh-CN", page=1)
        self.assertTrue(url.startswith("https://api.themoviedb.org/3/tv/popular?"))

    def test_http_error_becomes_a_structured_failure(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                io.BytesIO(json.dumps({"status_message": "Invalid API key"}).encode("utf-8")),
            )

        entry = trending_source.fetch_list("popular_movie", "BAD", opener=opener)
        self.assertEqual(entry["status"], 401)
        self.assertEqual(entry["items"], [])
        self.assertEqual(entry["label"], "流行电影")
        self.assertIn("Invalid API key", entry["error"])

    def test_network_error_becomes_a_structured_failure(self):
        def opener(request, timeout=None):
            raise OSError("connection refused")

        entry = trending_source.fetch_list("popular_movie", "KEY", opener=opener)
        self.assertEqual(entry["status"], 0)
        self.assertEqual(entry["items"], [])
        self.assertIn("connection refused", entry["error"])

    def test_non_200_without_raising_is_still_a_failure(self):
        entry = trending_source.fetch_list(
            "popular_movie", "KEY", opener=_identity_opener(b"{}", status=503)
        )
        self.assertEqual(entry["status"], 503)
        self.assertEqual(entry["items"], [])
        self.assertIn("503", entry["error"])

    def test_broken_json_and_unexpected_shape_are_failures(self):
        broken = trending_source.fetch_list(
            "popular_movie", "KEY", opener=_identity_opener(b"not json")
        )
        self.assertEqual(broken["status"], 0)
        self.assertIn("invalid json", broken["error"])

        shape = trending_source.fetch_list(
            "popular_movie",
            "KEY",
            opener=_identity_opener(json.dumps({"results": "nope"}).encode("utf-8")),
        )
        self.assertEqual(shape["status"], 0)
        self.assertIn("unexpected payload shape", shape["error"])

    def test_unknown_list_id_is_a_failure_not_an_exception(self):
        entry = trending_source.fetch_list("no_such_list", "KEY")
        self.assertEqual(entry["status"], 0)
        self.assertEqual(entry["items"], [])
        self.assertIn("unknown list id", entry["error"])

    def test_missing_api_key_never_sends_a_request(self):
        fake = FakeTMDb(per_list=10)
        entry = trending_source.fetch_list("popular_movie", "  ", opener=fake.opener)
        self.assertEqual(entry["status"], 0)
        self.assertIn("api_key is required", entry["error"])
        self.assertEqual(fake.calls, [])

    def test_payload_builder_is_shaped_like_tmdb(self):
        # 替身自己也得像 TMDb，否则上面的断言证明的是替身而不是被测代码
        payload = tmdb_payload("/3/movie/popular", 12)
        self.assertEqual(len(payload["results"]), 12)
        self.assertTrue(payload["results"][0]["release_date"])
        self.assertIn("poster_path", payload["results"][0])


if __name__ == "__main__":
    unittest.main()
