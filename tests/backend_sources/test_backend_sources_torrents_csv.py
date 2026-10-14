"""The second magnet channel: entry shape, snapshot freshness, degradation.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import json
import unittest
import urllib.parse

from tests.backend_sources._backend_sources_helpers import (
    FIXTURES,
    NOW,
    load_fixture,
    torrents_csv_source,
)

class TorrentsCsvTests(unittest.TestCase):
    """Entry shape, snapshot freshness, and degradation of the second channel."""

    def setUp(self):
        self.payload = load_fixture("torrents_csv_search.json")

    def test_entry_shape_matches_the_shared_candidate_keys(self):
        entries = torrents_csv_source.parse_payload(self.payload, now=NOW)
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(
                set(entry),
                {"info_hash", "name", "size", "size_gb", "file_count", "seeders",
                 "leechers", "created_at", "seeders_snapshot_unix",
                 "seeders_snapshot_at", "seeders_snapshot_age_days", "sources", "magnet"},
                # 源模块保留自己的字段名；写进池子的名字由 search_pool 映射
                # （见 torrents_csv_to_candidate：seeders -> external_seeders）。
            )
            self.assertTrue(torrents_csv_source.is_usable_hash(entry["info_hash"]))
            self.assertEqual(entry["info_hash"], entry["info_hash"].upper())
            self.assertEqual(entry["magnet"], f"magnet:?xt=urn:btih:{entry['info_hash']}")
            self.assertEqual(entry["sources"], [torrents_csv_source.SOURCE_NAME])
            self.assertIsInstance(entry["size"], int)
            self.assertIsNone(entry["file_count"])  # this channel publishes no file list

    def test_snapshot_age_tracks_scraped_date(self):
        entry = torrents_csv_source.parse_payload(self.payload, now=NOW)[0]
        scraped = self.payload["torrents"][0]["scraped_date"]
        self.assertAlmostEqual(entry["seeders_snapshot_age_days"],
                               round((NOW - scraped) / 86400.0, 2), places=2)
        self.assertIsInstance(entry["seeders"], int)

    def test_missing_swarm_fields_do_not_raise(self):
        entry = torrents_csv_source.normalise_entry(
            {"infohash": "c" * 40, "name": "no swarm fields"}, now=NOW)
        self.assertIsNone(entry["seeders"])
        self.assertIsNone(entry["seeders_snapshot_age_days"])
        self.assertIsNone(entry["seeders_snapshot_at"])
        self.assertEqual(entry["size"], 0)

    def test_short_or_bogus_hashes_are_dropped(self):
        entries = torrents_csv_source.parse_payload(
            {"torrents": [{"infohash": "abc", "name": "short"},
                          {"infohash": "z" * 40, "name": "not hex"},
                          {"name": "no hash"}]}, now=NOW)
        self.assertEqual(entries, [])

    def test_unreachable_channel_degrades_to_a_status(self):
        def boom(url, timeout):
            raise OSError("network is down")

        result = torrents_csv_source.search("狂飙", fetch=boom)
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["entries"], [])
        self.assertIn("network is down", result["message"])

    def test_error_response_and_bad_json_degrade_to_a_status(self):
        self.assertEqual(
            torrents_csv_source.search("狂飙", fetch=lambda url, timeout: (503, "busy"))["status"],
            503)
        broken = torrents_csv_source.search("狂飙", fetch=lambda url, timeout: (200, "{not json"))
        self.assertEqual(broken["status"], 0)
        self.assertEqual(broken["entries"], [])

    def test_request_uses_q_and_size(self):
        seen = {}

        def fetch(url, timeout):
            seen["url"] = url
            seen["timeout"] = timeout
            return 200, json.dumps(self.payload)

        result = torrents_csv_source.search("狂飙", limit=25, timeout=9, fetch=fetch)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
        self.assertEqual(query["q"], ["狂飙"])
        self.assertEqual(query["size"], ["25"])
        self.assertEqual(seen["timeout"], 9)
        self.assertEqual(result["status"], 200)

    def test_csv_dump_and_rows_parse_without_the_network(self):
        entries = torrents_csv_source.read_csv_dump(FIXTURES / "torrents_csv_dump.csv", now=NOW)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["seeders"], 42)
        self.assertIsNone(entries[0]["file_count"])
        with_count = torrents_csv_source.parse_csv_rows(
            [{"infohash": "d" * 40, "name": "with count", "size_bytes": 1024,
              "file_count": 7, "seeders": 3, "scraped_date": int(NOW) - 86400}], now=NOW)
        self.assertEqual(with_count[0]["file_count"], 7)
        self.assertEqual(with_count[0]["size"], 1024)
        self.assertAlmostEqual(with_count[0]["seeders_snapshot_age_days"], 1.0, places=2)
