"""Paging gates: `has_more`, the two-empty-pages stop, and the wall-clock budget.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.backend_sources._backend_sources_helpers import (
    FakeClock,
    FakeHttp,
    canned_pages,
    fixture_hashes,
    search_pool,
)

class PagingGateTests(unittest.TestCase):
    """has_more, the two-empty-pages stop, and the wall-clock budget."""

    def setUp(self):
        self.pages = canned_pages("漫长的季节")
        self.offsets = sorted(self.pages)
        self.total_unique = len(
            {hash_ for offset in self.offsets for hash_ in fixture_hashes(self.pages[offset])}
        )

    def paged(self, http, **kwargs):
        kwargs.setdefault("request_limit", 10)
        kwargs.setdefault("sleep", 0)
        with mock.patch.object(search_pool.urllib.request, "urlopen", http):
            return search_pool.fetch_query_pages("漫长的季节", **kwargs)

    def test_stops_when_has_more_turns_false(self):
        # offset 20 carries has_more=false: the pager must not ask for a 4th page.
        http = FakeHttp(dict(self.pages))
        outcome = self.paged(http, pages=5, workers=1, budget=None)
        self.assertEqual(outcome["stopped_by"], "has_more")
        self.assertEqual([page["offset"] for page in outcome["pages"]], [0, 10, 20])
        self.assertEqual(outcome["requests"], 3)
        self.assertEqual(len(http.requests), 3)

    def test_has_more_true_exhausts_the_page_cap(self):
        http = FakeHttp(dict(self.pages))
        outcome = self.paged(http, pages=2, workers=1, budget=None)
        self.assertEqual(outcome["stopped_by"], "pages_exhausted")
        self.assertEqual(outcome["requests"], 2)

    def test_two_pages_without_new_hashes_stop_the_pager(self):
        duplicate = self.pages[0]["data"]["torrents"][0]
        repeated = {"status": 200,
                    "data": {"keywords": [], "torrents": [duplicate], "has_more": True}}
        by_offset = {0: self.pages[0], 10: repeated, 20: repeated, 30: self.pages[20]}
        http = FakeHttp(by_offset)
        outcome = self.paged(http, pages=4, workers=1, budget=None)
        self.assertEqual(outcome["stopped_by"], "no_new")
        self.assertEqual([page["offset"] for page in outcome["pages"]], [0, 10, 20])
        self.assertEqual([page["fresh"] for page in outcome["pages"]],
                         [len(fixture_hashes(self.pages[0])), 0, 0])
        self.assertEqual(outcome["requests"], 3)

    def test_fresh_counts_only_hashes_the_pool_lacks(self):
        # Page 10 already repeats one record of page 0 (see the fixture recorder),
        # so a shared `seen` set has to make the fresh count drop by exactly one.
        overlap = set(fixture_hashes(self.pages[0])) & set(fixture_hashes(self.pages[10]))
        self.assertTrue(overlap, "fixture no longer exercises the de-dupe path")
        http = FakeHttp(dict(self.pages))
        outcome = self.paged(http, pages=2, workers=1, budget=None)
        self.assertEqual(outcome["pages"][1]["fresh"],
                         len(fixture_hashes(self.pages[10])) - len(overlap))

    def test_wave_fetches_concurrently_but_consumes_in_offset_order(self):
        duplicate = self.pages[0]["data"]["torrents"][0]
        repeated = {"status": 200,
                    "data": {"keywords": [], "torrents": [duplicate], "has_more": True}}
        by_offset = {0: self.pages[0], 10: repeated, 20: repeated}
        http = FakeHttp(by_offset)
        outcome = self.paged(http, pages=3, workers=4, budget=None)
        # One wave, three requests, but consumption still stops on the second empty page.
        self.assertEqual(outcome["requests"], 3)
        self.assertEqual(len(http.requests), 3)
        self.assertEqual(outcome["stopped_by"], "no_new")
        self.assertEqual([page["offset"] for page in outcome["pages"]], [0, 10, 20])

    def test_budget_gate_clamps_the_request_timeout(self):
        clock = FakeClock()
        http = FakeHttp(dict(self.pages), delay=10.0, clock=clock)
        budget = search_pool.Deadline(5.0, clock=clock)
        outcome = self.paged(http, pages=4, workers=1, budget=budget, timeout=20)
        self.assertEqual(outcome["stopped_by"], "budget")
        self.assertLessEqual(outcome["elapsed_secs"], 5.0)
        self.assertEqual(outcome["requests"], 1)
        self.assertEqual(http.requests[0]["timeout"], 5.0)

    def test_failed_page_is_skipped_not_fatal(self):
        http = FakeHttp(dict(self.pages))
        del http.by_offset[10]
        outcome = self.paged(http, pages=3, workers=1, budget=None)
        # offset 10 is lost, but the pages after it still land: one slow page
        # must not abandon the keyword.
        self.assertEqual([page["offset"] for page in outcome["pages"]], [0, 20])
        self.assertEqual(outcome["stopped_by"], "has_more")
        self.assertEqual([error["offset"] for error in outcome["errors"]], [10])

    def test_page_error_limit_stops_the_keyword(self):
        http = FakeHttp(dict(self.pages))
        del http.by_offset[10]
        del http.by_offset[20]
        outcome = self.paged(http, pages=4, workers=1, budget=None, error_limit=2)
        self.assertEqual(outcome["stopped_by"], "error")
        self.assertEqual([page["offset"] for page in outcome["pages"]], [0])
        self.assertEqual(len(outcome["errors"]), 2)
        self.assertEqual(outcome["requests"], 3)
