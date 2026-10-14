"""Merge rules and the external-channel report: empty values never overwrite
known ones, and a provenance-only merge leaves identity alone.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.backend_sources._backend_sources_helpers import (
    FakeClock,
    NOW,
    load_fixture,
    search_pool,
    torrents_csv_source,
)

class MergeAndReportTests(unittest.TestCase):
    """The merge path keeps both channels' data and never clobbers `sources`."""

    def setUp(self):
        self.payload = load_fixture("torrents_csv_search.json")
        self.entries = torrents_csv_source.parse_payload(self.payload, now=NOW)

    def test_merge_adds_candidates_with_external_seeders(self):
        candidates = {}
        fresh = search_pool.merge_external_entries(candidates, self.entries, "狂飙")
        self.assertEqual(fresh, len(self.entries))
        self.assertEqual(len(candidates), len(self.entries))
        for candidate in candidates.values():
            self.assertEqual(candidate["external_sources"], ["torrents-csv"])
            self.assertIsInstance(candidate["external_seeders"], int)
            self.assertIsInstance(candidate["external_seeders_age_days"], float)
            # 外部读数绝不冒充 announce 的 seeds（契约第 3 条）。
            self.assertNotIn("seeds", candidate)
            self.assertNotIn("seeders", candidate)
            self.assertNotIn("sources", candidate)  # probe's extraction labels are untouched
            self.assertTrue(candidate["magnet"].startswith("magnet:?xt=urn:btih:"))
            # F1：没有文件清单的条目不得带 metadata_at（否则 probe 不走 metadata）
            self.assertNotIn("metadata_at", candidate)

    def test_merge_enriches_a_diao_hit_without_losing_its_file_list(self):
        shared = self.entries[0]["info_hash"]
        candidates = {shared: {"ih": shared, "name": "from diao", "videos": [{"index": 1}],
                               "main_video_gb": 3.5, "source": "diao"}}
        search_pool.merge_external_entries(candidates, self.entries, "狂飙")
        enriched = candidates[shared]
        self.assertEqual(enriched["name"], "from diao")
        self.assertEqual(enriched["main_video_gb"], 3.5)
        self.assertEqual(enriched["external_seeders"], self.entries[0]["seeders"])
        self.assertEqual(enriched["external_seeders_age_days"],
                         self.entries[0]["seeders_snapshot_age_days"])
        self.assertEqual(enriched["external_sources"], ["torrents-csv"])

    def external_site(self, kind="torrents-csv", base_url="http://x", name="外部站"):
        return {"name": name, "kind": kind, "base_url": base_url, "web_search_url": ""}

    def test_expired_budget_skips_the_second_source(self):
        args = mock.Mock(extra_source_limit=10, extra_source_timeout=20)
        report = search_pool.collect_external_source(
            args, self.external_site(), ["狂飙"], {}, search_pool.Deadline(0.0))
        self.assertEqual(report["status"], "budget-exhausted")
        self.assertEqual(report["new"], 0)

    def test_second_source_stops_when_its_slice_runs_out(self):
        # A 3s slice with two aliases: the first query spends the whole slice
        # (the fake advances the clock), so the second is never sent.
        clock = FakeClock()
        queried = []

        def slow(query, **kwargs):
            queried.append(query)
            clock.advance(3.0)
            return {"status": 200, "message": "", "query": query,
                    "entries": torrents_csv_source.parse_payload(
                        load_fixture("torrents_csv_search.json")), "elapsed_secs": 3.0}

        args = mock.Mock(extra_source_limit=10, extra_source_timeout=20)
        with mock.patch.object(torrents_csv_source, "search", slow):
            report = search_pool.collect_external_source(
                args, self.external_site(), ["狂飙", "The Knockout"], {},
                search_pool.Deadline(3.0, clock=clock))
        self.assertEqual(queried, ["狂飙"])
        self.assertEqual(report["budget_secs"], 3.0)
        self.assertIn("budget exhausted", report["message"])

    def test_unknown_station_kind_is_reported_not_crashed(self):
        """配置层会先拦掉未知 kind；这层也守着，免得绕过配置时静默丢站。"""
        args = mock.Mock(extra_source_limit=10, extra_source_timeout=20)
        report = search_pool.collect_external_source(
            args, self.external_site(kind="nope"), ["x"], {}, search_pool.Deadline(10.0))
        self.assertEqual(report["status"], "unknown-source")
        self.assertEqual(report["kind"], "nope")

    def test_failed_channel_leaves_l1_candidates_alone(self):
        args = mock.Mock(extra_source_limit=10, extra_source_timeout=5)
        candidates = {"A" * 40: {"ih": "A" * 40, "name": "from diao", "main_video_gb": 2.0}}
        with mock.patch.object(torrents_csv_source, "search",
                               lambda *a, **k: {"status": 0, "message": "boom", "entries": []}):
            report = search_pool.collect_external_source(
                args, self.external_site(), ["狂飙"], candidates, search_pool.Deadline(10.0))
        self.assertEqual(report["status"], 0)
        self.assertEqual(report["new"], 0)
        self.assertEqual(list(candidates), ["A" * 40])


class MergeRuleTests(unittest.TestCase):
    """Each merge rule pinned on its own, so removing one is visible by itself.

    The integration-style F2 case passes with either protection alone, which
    would hide a regression in the other one; these cases isolate them.
    """

    def known_entry(self):
        return {
            "name": "Known.Release.2023.1080p.WEB-DL.x264-GRP.mkv",
            "videos": [{"index": 0, "path": "p.mkv", "name": "p.mkv", "size": 8053063680}],
            "main_video_gb": 7.5, "size_gb": 7.5, "nfiles": 1, "main_video_ext": ".mkv",
            "metadata_at": NOW - 10 * 86400, "seeds": 30,
        }

    def test_empty_values_never_overwrite_known_values(self):
        entry = self.known_entry()
        search_pool.merge_pool_entry(entry, {
            "videos": [], "main_video_gb": 0.0, "nfiles": 0, "size_gb": 0.0,
            "name": "", "main_video_ext": "",
        })
        self.assertEqual(entry["main_video_gb"], 7.5)
        self.assertEqual(entry["nfiles"], 1)
        self.assertTrue(entry["videos"])
        self.assertEqual(entry["name"], "Known.Release.2023.1080p.WEB-DL.x264-GRP.mkv")
        self.assertEqual(entry["main_video_ext"], ".mkv")

    def test_non_empty_values_still_update(self):
        entry = self.known_entry()
        search_pool.merge_pool_entry(entry, {"main_video_gb": 9.0, "nfiles": 3})
        self.assertEqual(entry["main_video_gb"], 9.0)
        self.assertEqual(entry["nfiles"], 3)

    def test_metadata_at_is_not_refreshed_by_a_merge(self):
        entry = self.known_entry()
        old = entry["metadata_at"]
        search_pool.merge_pool_entry(entry, {"metadata_at": NOW, "seeds": 99})
        self.assertEqual(entry["metadata_at"], old)
        self.assertEqual(entry["seeds"], 99)
        # 但一个还没有 metadata 的条目，允许在（有文件清单的）新条目里带上它
        fresh = {"name": "x"}
        search_pool.merge_pool_entry(fresh, {"metadata_at": NOW, "videos": [{"index": 0}]})
        self.assertEqual(fresh["metadata_at"], NOW)

    def test_provenance_only_merge_leaves_identity_alone(self):
        entry = self.known_entry()
        name = entry["name"]
        search_pool.merge_pool_entry(
            entry,
            {"name": "[index] other name", "size_gb": 1.0,
             "external_seeders": 12, "external_seeders_age_days": 22.7,
             "external_sources": ["torrents-csv"]},
            allow_metadata_at=False, keys=search_pool.PROVENANCE_MERGE_KEYS)
        self.assertEqual(entry["name"], name)
        self.assertEqual(entry["size_gb"], 7.5)
        self.assertEqual(entry["external_seeders"], 12)
        self.assertEqual(entry["external_sources"], ["torrents-csv"])

    def test_provenance_lists_are_unioned(self):
        entry = self.known_entry()
        entry["external_sources"] = ["torrents-csv"]
        search_pool.merge_pool_entry(
            entry, {"external_sources": ["torrents-csv", "pansou"]},
            keys=search_pool.PROVENANCE_MERGE_KEYS)
        self.assertEqual(entry["external_sources"], ["torrents-csv", "pansou"])
