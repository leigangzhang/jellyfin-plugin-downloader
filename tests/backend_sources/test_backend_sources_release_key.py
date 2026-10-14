"""The de-dupe dimension on top of the info hash.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import unittest

from tests.backend_sources._backend_sources_helpers import search_pool

class ReleaseKeyTests(unittest.TestCase):
    """The de-dupe dimension on top of the info hash."""

    def candidate(self, name, info_hash, source="diao", resolution="2160p"):
        return {"ih": info_hash, "name": name, "source": source, "main_video_gb": 1.0,
                "resolution": resolution}

    def test_marketing_domains_do_not_split_one_release(self):
        left = search_pool.release_key(self.candidate(
            "【高清剧集网发布 www.BPHDTV.com】漫长的季节[全12集][中文字幕].2160p", "A" * 40))
        right = search_pool.release_key(self.candidate(
            "【高清剧集网发布 www.DDHDTV.com】漫长的季节[全12集][中文字幕].2160p", "B" * 40))
        self.assertEqual(left, right)
        self.assertTrue(left.endswith("|2160p|diao"))

    def test_different_resolution_or_source_stays_apart(self):
        base = "漫长的季节[全12集][中文字幕]"
        keys = {
            search_pool.release_key(self.candidate(base + ".2160p", "A" * 40)),
            search_pool.release_key(self.candidate(base + ".1080p", "B" * 40)),
            search_pool.release_key(self.candidate(base + ".2160p", "C" * 40, source="torrents-csv")),
        }
        self.assertEqual(len(keys), 3)

    def test_collapse_keeps_one_representative_and_records_the_others(self):
        name = "漫长的季节[全12集][中文字幕].2160p"
        candidates = {
            "A" * 40: self.candidate(name, "A" * 40),
            "B" * 40: self.candidate(name, "B" * 40),
        }
        for key, value in candidates.items():
            value["release_key"] = search_pool.release_key(value)
            value["duplicate_hashes"] = []
        kept, dropped = search_pool.collapse_release_duplicates(candidates)
        self.assertEqual(len(kept), 1)
        representative = next(iter(kept.values()))
        self.assertEqual(representative["duplicate_hashes"], ["B" * 40])
        self.assertEqual(list(dropped.values()), [["B" * 40]])
