"""The call contract of `search_pool.search_api`.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

from tests.backend_sources._backend_sources_helpers import FakeHttp, load_fixture, search_pool

class SearchApiSignatureTests(unittest.TestCase):
    """The index is picky about the call; the existing signature must not move."""

    def test_signature_is_unchanged(self):
        # Paging is bolted on around this call, never through it: the index is
        # picky (403 without a UA, 400 with `q=`), so the contract must not move.
        parameters = inspect.signature(search_pool.search_api).parameters
        self.assertEqual(list(parameters), ["query", "limit", "offset", "base_url", "timeout"])
        self.assertEqual(parameters["limit"].default, search_pool.MAX_LIMIT)
        self.assertEqual(parameters["offset"].default, 0)

    def test_request_uses_keyword_ua_and_reads_has_more(self):
        payload = load_fixture("diao_page_offset0.json")
        http = FakeHttp({0: payload})
        with mock.patch.object(search_pool.urllib.request, "urlopen", http):
            result = search_pool.search_api("狂飙", limit=7, offset=0, timeout=5)
        request = http.requests[0]
        self.assertIn("keyword=%E7%8B%82%E9%A3%99", request["url"])
        self.assertNotIn("q=", request["url"])
        self.assertIn("limit=7", request["url"])
        self.assertEqual(request["user_agent"], search_pool.USER_AGENT)
        self.assertEqual(result["status"], 200)
        self.assertIs(result["has_more"], True)

    def test_has_more_is_false_when_the_index_says_so(self):
        payload = load_fixture("diao_page_offset20.json")
        http = FakeHttp({20: payload})
        with mock.patch.object(search_pool.urllib.request, "urlopen", http):
            result = search_pool.search_api("漫长的季节", offset=20)
        self.assertIs(result["has_more"], False)
