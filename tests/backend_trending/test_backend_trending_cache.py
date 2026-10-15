"""缓存层：TTL 内命中不触网、过期后重新取、键含语言与榜单组合、原子写不留 `.tmp`。

时钟是注入的（`now=`），所以这里测的是 TTL 判断本身，不是真实等待。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.backend_trending import (
    FakeTMDb,
    temp_data_root,
    trending_cache,
    trending_source,
    trending_tmp_files,
)

LANG = "zh-CN"
IDS = list(trending_source.LIST_ORDER)
TTL = trending_cache.CACHE_TTL_SECS


class TrendingCacheTests(unittest.TestCase):
    def test_second_call_inside_ttl_does_not_touch_tmdb(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root():
            first = trending_cache.load_lists(LANG, IDS, "KEY", opener=fake.opener, now=1000.0)
            self.assertFalse(first["stale"])
            self.assertEqual([entry["id"] for entry in first["lists"]], IDS)
            calls_after_first = len(fake.calls)
            self.assertEqual(calls_after_first, len(IDS))

            second = trending_cache.load_lists(
                LANG, IDS, "KEY", opener=fake.opener, now=1000.0 + TTL - 1
            )
            self.assertEqual(len(fake.calls), calls_after_first)  # 没打 TMDb
            self.assertFalse(second["stale"])
            self.assertEqual(second["lists"], first["lists"])

    def test_expired_entry_is_fetched_again_and_cache_is_rewritten(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root():
            trending_cache.load_lists(LANG, IDS, "KEY", opener=fake.opener, now=1000.0)
            self.assertEqual(len(fake.calls), len(IDS))

            refreshed = trending_cache.load_lists(
                LANG, IDS, "KEY", opener=fake.opener, now=1000.0 + TTL + 1
            )
            self.assertEqual(len(fake.calls), 2 * len(IDS))
            self.assertFalse(refreshed["stale"])
            with open(trending_cache.cache_path(), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            key = trending_cache.cache_key(LANG, IDS)
            self.assertEqual(payload["entries"][key]["fetched_at"], 1000.0 + TTL + 1)

    def test_cache_key_covers_language_and_the_list_set(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root():
            trending_cache.load_lists(LANG, IDS, "KEY", opener=fake.opener, now=1000.0)
            baseline = len(fake.calls)

            # 换语言：另一份缓存，必须重新取
            trending_cache.load_lists("en-US", IDS, "KEY", opener=fake.opener, now=1000.0)
            self.assertEqual(len(fake.calls), baseline + len(IDS))

            # 换榜单组合：也是另一份缓存
            trending_cache.load_lists(
                LANG, ["popular_movie"], "KEY", opener=fake.opener, now=1000.0
            )
            self.assertEqual(len(fake.calls), baseline + len(IDS) + 1)

            # 回到原始组合：仍有缓存，不再触网
            trending_cache.load_lists(LANG, IDS, "KEY", opener=fake.opener, now=1000.0)
            self.assertEqual(len(fake.calls), baseline + len(IDS) + 1)

    def test_atomic_write_leaves_no_tmp_files_and_a_parsable_cache(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root() as root:
            trending_cache.load_lists(LANG, IDS, "KEY", opener=fake.opener, now=1000.0)
            self.assertEqual(trending_tmp_files(root), [])
            names = sorted(os.listdir(trending_cache.cache_dir()))
            self.assertEqual(names, ["cache.json"])
            with open(trending_cache.cache_path(), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["version"], trending_cache.CACHE_VERSION)
            self.assertIn(trending_cache.cache_key(LANG, IDS), payload["entries"])

    def test_broken_cache_file_is_treated_as_empty(self):
        fake = FakeTMDb(per_list=20)
        with temp_data_root():
            os.makedirs(trending_cache.cache_dir(), exist_ok=True)
            with open(trending_cache.cache_path(), "w", encoding="utf-8") as handle:
                handle.write("{ 坏掉的 json")
            self.assertEqual(trending_cache.read_cache(), {})

            # 坏缓存不阻塞取源：照样能取到并重写一份好的
            result = trending_cache.load_lists(LANG, IDS, "KEY", opener=fake.opener, now=1000.0)
            self.assertFalse(result["stale"])
            self.assertNotEqual(trending_cache.read_cache(), {})

    def test_partial_failure_does_not_overwrite_a_good_cache(self):
        good = FakeTMDb(per_list=20)
        with temp_data_root():
            warm = trending_cache.load_lists(LANG, IDS, "KEY", opener=good.opener, now=1000.0)
            with open(trending_cache.cache_path(), "r", encoding="utf-8") as handle:
                before = handle.read()

            broken = FakeTMDb(error=OSError("network is unreachable"))
            result = trending_cache.load_lists(
                LANG, IDS, "KEY", opener=broken.opener, now=1000.0 + TTL + 1
            )
            self.assertTrue(result["stale"])
            self.assertEqual(result["lists"], warm["lists"])
            with open(trending_cache.cache_path(), "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), before)


if __name__ == "__main__":
    unittest.main()
