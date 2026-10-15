"""A9（后端侧）：重复关注返回 exists 且**不重写**清单文件。

「不重写」只有对着真文件才测得出来：比对内容字节与 `st_mtime_ns` —— 重写同样内容
也会让 mtime 动，而客户端与后续测试都靠它判「清单有没有变」。
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tests.backend_trending import temp_data_root, trending_tmp_files, trending_watchlist

MOVIE = {"tmdb_id": 550, "title": "Fight Club", "year": 1999, "kind": "movie"}


class TrendingWatchlistTests(unittest.TestCase):
    def test_first_watch_writes_and_duplicate_watch_does_not_rewrite(self):
        with temp_data_root():
            path = trending_watchlist.watchlist_path()
            first = trending_watchlist.add_watch(MOVIE, now=1700000000)
            self.assertFalse(first["exists"])
            self.assertTrue(first["written"])
            self.assertTrue(os.path.exists(path))

            before_bytes = Path(path).read_bytes()
            before_mtime = os.stat(path).st_mtime_ns

            again = trending_watchlist.add_watch(MOVIE, now=1700000999)
            self.assertTrue(again["exists"])
            self.assertFalse(again["written"])
            self.assertEqual(Path(path).read_bytes(), before_bytes)
            self.assertEqual(os.stat(path).st_mtime_ns, before_mtime)

            items = trending_watchlist.read_watchlist()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["tmdb_id"], 550)
            self.assertEqual(items[0]["title"], "Fight Club")
            self.assertEqual(items[0]["year"], 1999)
            self.assertEqual(items[0]["kind"], "movie")
            self.assertEqual(items[0]["added_at"], 1700000000)

    def test_stored_entry_keeps_at_least_the_documented_fields(self):
        with temp_data_root():
            trending_watchlist.add_watch(MOVIE, now=1700000000)
            item = trending_watchlist.read_watchlist()[0]
            for key in ("tmdb_id", "title", "year", "kind", "added_at"):
                self.assertIn(key, item)

    def test_string_and_int_ids_are_the_same_entry(self):
        with temp_data_root():
            trending_watchlist.add_watch({**MOVIE, "tmdb_id": "550"}, now=1700000000)
            again = trending_watchlist.add_watch(MOVIE, now=1700000001)
            self.assertTrue(again["exists"])
            self.assertFalse(again["written"])
            self.assertEqual(trending_watchlist.watchlist_ids(), [550])

    def test_second_entry_is_appended_and_ids_stay_in_order(self):
        with temp_data_root():
            trending_watchlist.add_watch(MOVIE, now=1700000000)
            second = trending_watchlist.add_watch(
                {"tmdb_id": 1399, "title": "权游", "year": 2011, "kind": "tv"},
                now=1700000001,
            )
            self.assertFalse(second["exists"])
            self.assertTrue(second["written"])
            self.assertEqual(trending_watchlist.watchlist_ids(), [550, 1399])
            # "tv" 归一到统一条目的类型取值
            self.assertEqual(trending_watchlist.read_watchlist()[1]["kind"], "series")

    def test_missing_id_raises_value_error(self):
        with temp_data_root():
            with self.assertRaises(ValueError):
                trending_watchlist.add_watch({"title": "无 id"})
            self.assertFalse(os.path.exists(trending_watchlist.watchlist_path()))

    def test_missing_or_broken_file_reads_as_an_empty_list(self):
        with temp_data_root():
            self.assertEqual(trending_watchlist.read_watchlist(), [])
            self.assertEqual(trending_watchlist.watchlist_ids(), [])

            os.makedirs(os.path.dirname(trending_watchlist.watchlist_path()), exist_ok=True)
            with open(trending_watchlist.watchlist_path(), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"items": "not a list"}))
            self.assertEqual(trending_watchlist.read_watchlist(), [])

            with open(trending_watchlist.watchlist_path(), "w", encoding="utf-8") as handle:
                handle.write("显然不是 json")
            self.assertEqual(trending_watchlist.read_watchlist(), [])

    def test_file_is_written_as_json_with_the_declared_version(self):
        with temp_data_root():
            trending_watchlist.add_watch(MOVIE, now=1700000000)
            with open(trending_watchlist.watchlist_path(), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["version"], trending_watchlist.WATCHLIST_VERSION)
            self.assertEqual(len(payload["items"]), 1)

    def test_no_tmp_files_survive_the_write(self):
        with temp_data_root() as root:
            trending_watchlist.add_watch(MOVIE, now=1700000000)
            trending_watchlist.add_watch({**MOVIE, "tmdb_id": 1399}, now=1700000001)
            self.assertEqual(trending_tmp_files(root), [])

    def test_writes_land_under_the_data_root_not_next_to_the_source(self):
        with temp_data_root() as root:
            trending_watchlist.add_watch(MOVIE, now=1700000000)
            expected = os.path.join(root, "trending", "watchlist.json")
            self.assertEqual(trending_watchlist.watchlist_path(), expected)
            self.assertTrue(os.path.exists(expected))


if __name__ == "__main__":
    unittest.main()
