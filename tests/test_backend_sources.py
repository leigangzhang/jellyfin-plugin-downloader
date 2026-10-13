"""Offline contract tests for the two backend discovery sources.

Subject: `backend/search_pool.py` (diao.im paging + keyword ladder) and
`backend/torrents_csv_source.py` (the second magnet channel). Nothing here
touches the network: the HTTP boundary is replaced by a canned responder built
from the trimmed real responses in `fixtures/jmd_sources/`.

`tests/harness.py` is not used on purpose -- it loads the kit's own tools, and
this module's subject is the plugin backend, which lives outside that root. The
backend path is put on `sys.path` once, at import, so `search_pool`'s existing
flat `media_download_lib` import keeps working exactly as it does at runtime.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
import os
import sys
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jmd_sources"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import probe_magnets  # noqa: E402
import search_pool  # noqa: E402
import source_config  # noqa: E402
import torrents_csv_source  # noqa: E402

NOW = 1_800_000_000.0  # fixed reference so snapshot ages are deterministic


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_hashes(payload):
    return [str(item.get("hash") or "").upper() for item in payload["data"]["torrents"]]


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHttp:
    """urlopen stand-in: answers with the canned payload matching the requested offset.

    It also models request duration, capped by the timeout the caller asked for,
    so a wall-clock budget can be proven without a real sleep.
    """

    def __init__(self, by_offset, delay=0.0, clock=None, missing_status=None, default=None):
        self.by_offset = by_offset
        self.delay = delay
        self.clock = clock
        self.missing_status = missing_status
        # `default` answers every offset the map does not cover (used when one
        # canned page should stand in for a whole query).
        self.default = default
        self.requests = []
        self._lock = threading.Lock()

    def __call__(self, request, timeout=None):
        with self._lock:
            self.requests.append({"url": request.full_url, "timeout": timeout,
                                  "user_agent": request.get_header("User-agent")})
        if self.clock is not None:
            self.clock.advance(min(self.delay, float(timeout or self.delay)))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        offset = int(query.get("offset", ["0"])[0])
        payload = self.by_offset.get(offset, self.default)
        if payload is None:
            if self.missing_status is not None:
                raise search_pool.urllib.error.HTTPError(
                    request.full_url, self.missing_status, "missing", {}, None)
            raise OSError(f"no canned page for offset {offset}")
        return FakeResponse(json.dumps(payload).encode("utf-8"))


class FakeClock:
    """Manual monotonic clock for budget tests."""

    def __init__(self, start=0.0):
        self.value = start
        self._lock = threading.Lock()

    def __call__(self):
        return self.value

    def advance(self, seconds):
        with self._lock:
            self.value += seconds


def canned_pages(names):
    """offset -> payload, from the recorded fixtures (offsets 0/10/20)."""
    offsets = {"diao_page_offset0.json": 0,
               "diao_page_offset10.json": 10,
               "diao_page_offset20.json": 20}
    return {offset: load_fixture(name) for name, offset in offsets.items()}


def diao_payload(torrents, has_more=False):
    """A canned HTTP *body* for the diao index (`search_api` parses this shape)."""
    return {"status": 200,
            "data": {"keywords": [], "torrents": list(torrents), "has_more": has_more}}


def l1_torrent(info_hash, name, files=()):
    """One canned diao hit; `files` is what decides whether metadata is verified."""
    return {"hash": info_hash, "name": name, "size": 123456, "files": list(files),
            "files_count": len(files), "magnet_uri": f"magnet:?xt=urn:btih:{info_hash}"}


def external_result(entries):
    """A canned torrents-csv channel response."""
    return {"status": 200, "message": "", "query": "", "entries": list(entries),
            "elapsed_secs": 0.0}


def external_entry(info_hash=None, name=None, seeders=12, age_days=22.7, size=123456):
    """One torrents-csv entry in the shape `torrents_csv_source.search` returns."""
    resolved = info_hash or "E" * 40
    return {"info_hash": resolved, "name": name or f"External.Release.{resolved[:6]}.2160p",
            "size": size, "size_gb": round(size / (1024 ** 3), 3), "file_count": None,
            "seeders": seeders, "leechers": 1, "created_at": None,
            "seeders_snapshot_unix": int(NOW), "seeders_snapshot_at": "2026-09-20T00:00:00+00:00",
            "seeders_snapshot_age_days": age_days, "sources": ["torrents-csv"],
            "magnet": magnet_for(resolved)}


def magnet_for(info_hash):
    return f"magnet:?xt=urn:btih:{info_hash}"


DIAO_SITE = {"name": "diao.im", "kind": "diao",
             "base_url": "https://m.diao.im/api/search", "web_search_url": ""}


def sites_env(sites):
    """把站点清单编码成环境变量的形状（`source_config` 是唯一的读者）。"""
    return json.dumps(sites, ensure_ascii=False)


def run_search_main(argv, api, extra_search, env=None):
    """跑 `main()`：HTTP 边界与第二通道都换成桩，站点清单按 `env` 覆盖。

    两个环境变量**总是**被显式设成某个值（默认空串 = 用内置默认），这样宿主机上
    碰巧设了它们也不会改变测试行为。
    """
    full_env = {source_config.ENV_SITES: "", source_config.ENV_KEYWORDS: ""}
    full_env.update(env or {})
    stdout = io.StringIO()
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.dict(os.environ, full_env, clear=False), \
            mock.patch.object(search_pool.urllib.request, "urlopen", api), \
            mock.patch.object(torrents_csv_source, "search", extra_search), \
            contextlib.redirect_stdout(stdout):
        code = search_pool.main()
    return code, stdout.getvalue()


def run_main_with_fakes(argv, page, extra_entries, env=None):
    """Run `main()` with the HTTP boundary and the second channel stubbed.

    The L1 side goes through the real `search_api` on purpose:
    `fetch_query_pages` takes `search_api` as a default argument, so patching the
    module attribute would not reach the pager and the test would quietly talk to
    the live index instead. One canned page (`page`) answers every offset, and
    `has_more` is false, so each query costs exactly one request.
    """
    return run_search_main(
        argv, FakeHttp({}, default=page),
        lambda *a, **k: external_result(extra_entries), env=env)


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


class QueryLadderTests(unittest.TestCase):
    """锚点档（配置的 L1 关键字）排在最前，别名/年份/裸片名精度档排在它之后。"""

    def test_anchor_tier_leads_the_ladder(self):
        plan = search_pool.build_query_plan(
            "狂飙", ["The Knockout"], year="2023", collection_hints=["全集"],
            anchor_keywords=("高清剧集网", "DDHDTV"))
        # 每个片名 × 每条关键字各一条查询，且都排在别名/年份之前。片名在前、别名在后：
        # 索引里的名字是本地化片名，第一枪打它命中率最高。
        self.assertEqual(
            [step["query"] for step in plan[:4]],
            ["狂飙 高清剧集网", "狂飙 DDHDTV",
             "The Knockout 高清剧集网", "The Knockout DDHDTV"])
        self.assertEqual([step["tier"] for step in plan[:4]], ["anchor"] * 4)
        tiers = [step["tier"] for step in plan]
        self.assertLess(tiers.index("anchor"), tiers.index("alias-year"))
        self.assertEqual(plan[4], {"query": "The Knockout 2023", "tier": "alias-year"})

    def test_anchor_tier_keeps_the_configured_order(self):
        plan = search_pool.build_query_plan("Wednesday", anchor_keywords=("A", "B", "C"))
        self.assertEqual([step["query"] for step in plan[:3]],
                         ["Wednesday A", "Wednesday B", "Wednesday C"])

    def test_anchor_tier_runs_for_pure_cjk_titles_too(self):
        """关键字是配置项，没有「中英不混搭」限制：中文片名同样要跑锚点档。

        实测（2026-10-13）：`漫长的季节 DDHDTV` 6/6 命中该发布者，而 `漫长的季节 S01`
        一条都搜不到它——档位是搜到这家源的可靠方式，与片名的字符集无关。
        """
        plan = search_pool.build_query_plan("狂飙", year="2023", anchor_keywords=("DDHDTV",))
        self.assertEqual(plan[0], {"query": "狂飙 DDHDTV", "tier": "anchor"})
        tiers = [step["tier"] for step in plan]
        self.assertLess(tiers.index("anchor"), tiers.index("title"))

    def test_anchor_tier_runs_in_strict_mode_as_well(self):
        """最高优先级 = 两种模式都跑；严格按季的档位排在锚点档之后。

        代价写在这里，免得下次有人以为这是意外：锚点档不带季标记，所以严格按季的
        搜索会把同一部剧的其它季一起带回来（下游没有季标记过滤器）。
        """
        plan = search_pool.build_query_plan(
            "Wednesday", collection_hints=["S01"], strict_match=True,
            anchor_keywords=("DDHDTV",))
        self.assertEqual([step["tier"] for step in plan], ["anchor", "strict"])
        self.assertEqual([step["query"] for step in plan], ["Wednesday DDHDTV", "Wednesday S01"])

    def test_no_configured_keyword_means_no_anchor_tier(self):
        plan = search_pool.build_query_plan("Wednesday", year="2022")
        self.assertNotIn("anchor", [step["tier"] for step in plan])

    def test_without_alias_the_ladder_still_reaches_the_title(self):
        plan = search_pool.build_query_plan("流浪地球2", year="2023")
        self.assertEqual([step["query"] for step in plan[:2]], ["流浪地球2 2023", "流浪地球2"])
        self.assertEqual(plan[0]["tier"], "year")

    def test_strict_mode_puts_aliases_before_the_primary_title(self):
        plan = search_pool.build_query_plan(
            "狂飙", ["The Knockout"], collection_hints=["第1季", "S01"], strict_match=True)
        self.assertEqual([step["query"] for step in plan],
                         ["The Knockout S01", "狂飙 第1季", "狂飙 S01"])

    def test_cli_defaults_reflect_the_new_gates(self):
        with mock.patch.object(sys, "argv", ["search_pool.py", "狂飙"]):
            args = search_pool.parse_args()
        self.assertEqual(args.pages, search_pool.DEFAULT_PAGES)
        self.assertEqual(args.page_budget_secs, search_pool.DEFAULT_PAGE_BUDGET_SECS)
        self.assertEqual(args.page_workers, search_pool.DEFAULT_PAGE_WORKERS)
        self.assertEqual(args.no_new_pages, search_pool.DEFAULT_NO_NEW_PAGES)
        self.assertEqual(args.extra_source_reserve_secs,
                         search_pool.DEFAULT_EXTRA_SOURCE_RESERVE_SECS)
        self.assertEqual(args.page_errors, search_pool.DEFAULT_PAGE_ERRORS)
        self.assertFalse(args.collapse_releases)

    def test_station_list_is_the_only_switch_left(self):
        """站点由配置决定：`--extra-source` 这类单独开关已经删除，传了就该报错。"""
        with mock.patch.object(sys, "argv",
                               ["search_pool.py", "狂飙", "--extra-source", ""]):
            with self.assertRaises(SystemExit):
                search_pool.parse_args()


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


class EndToEndOfflineTests(unittest.TestCase):
    """`main()` over both channels with the HTTP boundary replaced."""

    def run_main(self, argv, api, extra_search, env=None):
        return run_search_main(argv, api, extra_search, env=env)

    def test_json_output_carries_seeders_and_both_channels(self):
        import tempfile

        pages = canned_pages("漫长的季节")
        api = FakeHttp({offset: payload for offset, payload in pages.items()})
        seen = {}

        def extra(query, **kwargs):
            seen["query"] = query
            return {"status": 200, "message": "", "query": query,
                    "entries": torrents_csv_source.parse_payload(load_fixture("torrents_csv_search.json")),
                    "elapsed_secs": 0.1}

        with tempfile.TemporaryDirectory() as tmp:
            json_out = Path(tmp) / "out.json"
            code, printed = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(json_out), "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, extra)
            self.assertEqual(code, 0)
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            candidate = next(item for item in payload["candidates"]
                             if item.get("external_sources") == ["torrents-csv"])
            self.assertIsInstance(candidate["external_seeders"], int)
            self.assertIsInstance(candidate["external_seeders_age_days"], float)
            self.assertEqual(payload["queries"][0]["stopped_by"], "has_more")
            self.assertLessEqual(payload["queries"][0]["elapsed_secs"],
                                 search_pool.DEFAULT_PAGE_BUDGET_SECS)
            self.assertEqual(payload["external_sources"][0]["status"], 200)
            # 站点与关键字来自配置（默认 = 内置的两个站、两条署名关键字）
            self.assertEqual([site["name"] for site in payload["sites"]],
                             [site["name"] for site in source_config.DEFAULT_SITES])
            self.assertEqual(payload["l1_keywords"], list(source_config.DEFAULT_ANCHOR_KEYWORDS))
            self.assertGreater(payload["external_sources"][0]["new"], 0)
            # The second source lives inside the reserved slice of the same
            # budget, so the per-keyword wall clock cannot overshoot the gate.
            self.assertEqual(payload["external_sources"][0]["budget_secs"],
                             search_pool.DEFAULT_EXTRA_SOURCE_RESERVE_SECS)
            self.assertLessEqual(payload["external_sources"][0]["elapsed_secs"],
                                 search_pool.DEFAULT_EXTRA_SOURCE_RESERVE_SECS)
            self.assertIn('"external_seeders"', json_out.read_text(encoding="utf-8"))
            self.assertIn("SEARCH_RESULT", printed)
            self.assertEqual(seen["query"], "漫长的季节")

    def test_dead_second_channel_still_delivers_l1_candidates(self):
        import tempfile

        api = FakeHttp(canned_pages("漫长的季节"))
        with tempfile.TemporaryDirectory() as tmp:
            json_out = Path(tmp) / "out.json"
            code, _ = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(json_out), "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, lambda *a, **k: {"status": 0, "message": "down", "entries": []})
            self.assertEqual(code, 0)
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            self.assertTrue(payload["candidates"])
            self.assertEqual(payload["external_sources"][0]["status"], 0)
            self.assertEqual(payload["external_sources"][0]["new"], 0)

    def test_station_list_decides_who_is_queried(self):
        """站点清单是唯一的开关：只配 diao 时，第二通道一次都不许被问。"""
        import tempfile

        api = FakeHttp(canned_pages("漫长的季节"))
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, lambda *a, **k: calls.append(a) or {"status": 200, "entries": []},
                env={source_config.ENV_SITES: sites_env([DIAO_SITE])})
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])
            self.assertIn('"sites": [{"name": "diao.im"', printed)

    def test_anchor_keywords_come_from_the_environment(self):
        """L1 关键字组合是配置：环境变量里的每条都要在查询里出现，且排在最前。"""
        import tempfile

        api = FakeHttp(canned_pages("漫长的季节"))
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, lambda *a, **k: {"status": 200, "message": "", "entries": []},
                env={source_config.ENV_SITES: sites_env([DIAO_SITE]),
                     source_config.ENV_KEYWORDS: json.dumps(["甲发布", "乙发布"])})
            self.assertEqual(code, 0)
            self.assertNotIn("SOURCE_CONFIG_ERROR", printed)
            self.assertIn("漫长的季节 甲发布", printed)
            self.assertIn("漫长的季节 乙发布", printed)
            first = next(line for line in printed.splitlines() if line.startswith("  ["))
            self.assertIn("[anchor   ]", first)
            self.assertIn("甲发布", first)

    def test_two_stations_do_not_share_the_no_new_pages_counter(self):
        """「连续两页没有新 hash 就停」是**每个站各自**的判据。

        两个索引经常有重叠，共用一份见过集合会让第二个站的第一页就被判成「没有新
        东西」，翻页提前结束。这里两个站返回同一份页面：每个站都该按自己的规则走完
        ——第一页有新 hash、之后连续两页没有 → 3 次请求后停。
        """
        import tempfile

        page = diao_payload(
            [l1_torrent("A" * 40, "Same.Release.mkv",
                        files=[{"index": 0, "path": "Same.Release.mkv",
                                "size": 4 * 1024 ** 3}])],
            has_more=True)
        api = FakeHttp({}, default=page)
        sites = [
            {"name": "A", "kind": "diao", "base_url": "https://a.example/api",
             "web_search_url": ""},
            {"name": "B", "kind": "diao", "base_url": "https://b.example/api",
             "web_search_url": ""},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool", "--sleep", "0",
                 "--pages", "4", "--page-workers", "1", "--no-new-pages", "2",
                 "--max-queries", "1",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, lambda *a, **k: {"status": 200, "entries": []},
                env={source_config.ENV_SITES: sites_env(sites),
                     source_config.ENV_KEYWORDS: "[]"})
            self.assertEqual(code, 0)
        per_host = {}
        for entry in api.requests:
            host = urllib.parse.urlparse(entry["url"]).netloc
            per_host[host] = per_host.get(host, 0) + 1
        self.assertEqual(per_host, {"a.example": 3, "b.example": 3}, per_host)

    def test_a_non_api_only_station_list_skips_the_ladder(self):
        """只配非 API 站时不该白跑阶梯；那一个站自己的通道必须照常被问。"""
        import tempfile

        api = FakeHttp(canned_pages("漫长的季节"))
        calls = []
        only_external = [{"name": "idx", "kind": "torrents-csv",
                          "base_url": "https://idx.example/api", "web_search_url": ""}]
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                api,
                lambda *a, **k: calls.append(a) or {
                    "status": 200, "message": "", "entries": torrents_csv_source.parse_payload(
                        load_fixture("torrents_csv_search.json"))},
                env={source_config.ENV_SITES: sites_env(only_external)})
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 1, "非 API 站应当被问恰好一次")
            self.assertNotIn("SEARCH_ERROR", printed)
            self.assertNotIn("  [anchor", printed)
            self.assertIn('"idx"', printed)

    def test_bad_station_config_is_reported_not_silently_defaulted(self):
        """坏配置必须走可见的失败路径，不许悄悄回落到默认站。"""
        import tempfile

        api = FakeHttp(canned_pages("漫长的季节"))
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, lambda *a, **k: {"status": 200, "entries": []},
                env={source_config.ENV_SITES: json.dumps([{"kind": "nope",
                                                           "base_url": "https://x/y"}])})
            self.assertEqual(code, 2)
            self.assertIn("SOURCE_CONFIG_ERROR", printed)

    def test_next_step_hint_does_not_recommend_the_retired_speed_probe(self):
        # The trial-run speed probe was rejected by the user and the console no
        # longer passes it; a hint that still suggests it would send the next
        # reader back down a retired path.
        import tempfile

        api = FakeHttp(canned_pages("漫长的季节"))
        with tempfile.TemporaryDirectory() as tmp:
            _, printed = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, lambda *a, **k: {"status": 200, "message": "",
                                      "entries": torrents_csv_source.parse_payload(
                                          load_fixture("torrents_csv_search.json"))})
        hint = next(line for line in printed.splitlines() if line.startswith("下一步:"))
        self.assertNotIn("--speed-probe", hint)
        self.assertIn("--budget-secs 720", hint)



class ExternalCandidateGateTests(unittest.TestCase):
    """F1/F2/F3 from the I4 integration review, pinned at search_pool's boundary."""

    def setUp(self):
        self.l1_hash = "A" * 40
        # 走源模块的 parse 路径（裸 API 记录的 `infohash`/`size_bytes` 不是候选字段）
        self.entries = torrents_csv_source.parse_payload(
            load_fixture("torrents_csv_search.json"), now=NOW)
        self.external = self.entries[0]
        self.external_hash = self.external["info_hash"]

    def test_f1_external_candidates_carry_no_metadata_at(self):
        import tempfile

        files = [{"index": 0, "path": "a.mkv", "size": 8053063680}]
        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "pool.json"
            json_out = Path(tmp) / "out.json"
            code, _ = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--pool-file", str(pool_path),
                 "--json-out", str(json_out), "--candidates-out", str(Path(tmp) / "out.magnets")],
                diao_payload([l1_torrent(self.l1_hash, "A.Release.mkv", files)]),
                [self.external])
            self.assertEqual(code, 0)
            by_hash = {item["ih"]: item for item in
                       json.loads(json_out.read_text(encoding="utf-8"))["candidates"]}
            # 有文件清单的 L1 候选才盖章；外部候选不盖章 → probe 才会去抓 metadata。
            self.assertIn("metadata_at", by_hash[self.l1_hash])
            self.assertNotIn("metadata_at", by_hash[self.external_hash])
            external = by_hash[self.external_hash]
            self.assertEqual(external["videos"], [])
            self.assertEqual(external["external_seeders"], self.external["seeders"])
            entries = json.loads(pool_path.read_text(encoding="utf-8"))["entries"]
            self.assertIn("metadata_at", entries[self.l1_hash])
            self.assertNotIn("metadata_at", entries[self.external_hash])

    def test_f2_merge_keeps_a_known_file_list_and_its_metadata_stamp(self):
        import tempfile

        known = "D" * 40
        old_stamp = NOW - 10 * 86400
        name = "Known.Release.2023.1080p.WEB-DL.x264-GRP.mkv"
        pool_payload = {"version": 1, "query": "x", "updated_at": "x", "entries": {
            known: {"name": name,
                    "videos": [{"index": 0, "path": "p.mkv", "name": "p.mkv",
                                "size": 8053063680}],
                    "main_video_gb": 7.5, "size_gb": 7.5, "nfiles": 1,
                    "main_video_ext": ".mkv", "junk_files": 0, "junk_bytes": 0,
                    "metadata_at": old_stamp, "health_at": old_stamp,
                    "seeds": 30, "peers": 40, "health_method": "announce"}}}
        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "pool.json"
            pool_path.write_text(json.dumps(pool_payload), encoding="utf-8")
            code, _ = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--pool-file", str(pool_path),
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                # 新搜索对这个 hash 只给出名字（空文件清单），外部通道也给同一个 hash
                diao_payload([l1_torrent(known, name)]),
                [external_entry(info_hash=known, name="[index] Known")])
            self.assertEqual(code, 0)
            after = json.loads(pool_path.read_text(encoding="utf-8"))["entries"][known]
            self.assertEqual(after["main_video_gb"], 7.5, "空清单把已知的 main_video_gb 覆盖掉了")
            self.assertTrue(after["videos"], "空清单把已知的 videos 覆盖掉了")
            self.assertEqual(after["nfiles"], 1, "空清单把已知的 nfiles 覆盖掉了")
            self.assertEqual(after["metadata_at"], old_stamp, "metadata_at 被合并刷新了")
            self.assertEqual(after["name"], name, "外部通道改写了已由 L1 描述过的条目标识")
            # 只补 swarm 与来源字段
            self.assertEqual(after["external_seeders"], 12)
            self.assertEqual(after["external_seeders_age_days"], 22.7)
            self.assertEqual(after["external_sources"], ["torrents-csv"])
            # announce 的 seeds 不被外部读数冒充
            self.assertEqual(after["seeds"], 30)

    def test_f2_external_only_candidate_cannot_rewrite_a_verified_entry(self):
        """Only an external candidate for this hash: the verified entry still wins.

        The sibling case above also has an L1 hit for the same hash, so its name
        survives through the L1 candidate. This one isolates main()'s own rule:
        a channel that knows no file list may only add provenance fields.
        """
        import tempfile

        known = "D" * 40
        old_stamp = NOW - 3 * 86400
        name = "Verified.Release.2023.2160p.WEB-DL.mkv"
        pool_payload = {"version": 1, "query": "x", "updated_at": "x", "entries": {
            known: {"name": name,
                    "videos": [{"index": 0, "path": "v.mkv", "name": "v.mkv",
                                "size": 16106127360}],
                    "main_video_gb": 15.0, "size_gb": 15.0, "nfiles": 2,
                    "main_video_ext": ".mkv", "metadata_at": old_stamp, "seeds": 44}}}
        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "pool.json"
            pool_path.write_text(json.dumps(pool_payload), encoding="utf-8")
            code, _ = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--pool-file", str(pool_path),
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                diao_payload([]),
                [external_entry(info_hash=known, name="[index] Known", seeders=7, age_days=22.7)])
            self.assertEqual(code, 0)
            after = json.loads(pool_path.read_text(encoding="utf-8"))["entries"][known]
            self.assertEqual(after["name"], name, "只有名字的索引改写了已核实的条目标识")
            self.assertEqual(after["main_video_gb"], 15.0)
            self.assertTrue(after["videos"])
            self.assertEqual(after["metadata_at"], old_stamp)
            self.assertEqual(after["external_seeders"], 7)
            self.assertEqual(after["external_sources"], ["torrents-csv"])

    def test_f3_strict_season_writes_external_magnets(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cand_out = Path(tmp) / "strict.magnets"
            code, printed = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--strict-match",
                 "--collection-hint", "第1季", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(cand_out)],
                diao_payload([]),
                [self.external])
            self.assertEqual(code, 0)
            result = json.loads(next(line for line in printed.splitlines()
                                     if line.startswith("SEARCH_RESULT")).split(" ", 1)[1])
            magnets = [line for line in cand_out.read_text(encoding="utf-8").splitlines()
                       if line and not line.startswith("#")]
            self.assertIn(magnet_for(self.external_hash), magnets)
            # 候选文件的行数必须与 SEARCH_RESULT 报的候选数一致（F3 的原始症状是
            # external=1 而文件为空）。
            self.assertEqual(len(magnets), result["candidates"])
            self.assertEqual(result["external"], len(magnets))

    def test_f3_candidate_file_carries_every_candidate_once(self):
        import tempfile

        files = [{"index": 0, "path": "a.mkv", "size": 8053063680}]
        with tempfile.TemporaryDirectory() as tmp:
            cand_out = Path(tmp) / "out.magnets"
            code, printed = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(cand_out)],
                diao_payload([l1_torrent(self.l1_hash, "A.Release.mkv", files),
                           l1_torrent("B" * 40, "B.Release.mkv", files)]),
                [self.external, external_entry(info_hash="E" * 40)])
            self.assertEqual(code, 0)
            result = json.loads(next(line for line in printed.splitlines()
                                     if line.startswith("SEARCH_RESULT")).split(" ", 1)[1])
            magnets = [line for line in cand_out.read_text(encoding="utf-8").splitlines()
                       if line and not line.startswith("#")]
            self.assertEqual(len(magnets), result["candidates"])
            self.assertEqual(len(set(magnets)), len(magnets))


class ExternalCandidateRankingTests(unittest.TestCase):
    """F1 end to end: what search_pool writes must let probe reach `ranked`.

    The I4 review's F1 symptom was an offline dry run that ended in rc=4 with
    zero metadata fetches, because the pool entry claimed `metadata_at` for a
    candidate that had no file list. This case runs the real probe over the real
    search_pool output with every network entry point stubbed, and asserts both
    halves: the probe *did* fetch metadata, and the candidate ended up ranked.
    """

    METADATA = {
        "name": "External.Only.2024.1080p.WEB-DL.x264-TEST", "size_gb": 7.5,
        "nfiles": 1, "main_video_gb": 7.5, "main_video_ext": ".mkv",
        "junk_files": 0, "junk_bytes": 0,
        "videos": [{"index": 0, "path": "e.mkv", "name": "e.mkv", "size": 8053063680}],
    }

    def setUp(self):
        self.external = torrents_csv_source.parse_payload(
            load_fixture("torrents_csv_search.json"), now=NOW)[0]
        self.external_hash = self.external["info_hash"]

    def run_probe(self, argv):
        """Run probe_magnets offline; returns (rc, calls, payload)."""
        calls = []

        def record(name, value):
            def _inner(*args, **kwargs):
                calls.append(name)
                return value
            return _inner

        patches = [
            mock.patch.object(probe_magnets, "fetch_metadata",
                              record("fetch_metadata", dict(self.METADATA))),
            mock.patch.object(probe_magnets, "announce_health", record(
                "announce_health",
                {"seeders": 151, "own_peers": 1, "external_peers": 150})),
            mock.patch.object(probe_magnets, "tracker_health", record("tracker_health", (150, 160))),
            mock.patch.object(probe_magnets, "public_ip", record("public_ip", "203.0.113.7")),
        ]
        stdout = io.StringIO()
        for patcher in patches:
            patcher.start()
        try:
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
                rc = probe_magnets.main()
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        payload = json.loads(Path(argv[argv.index("--json-out") + 1]).read_text(encoding="utf-8"))
        return rc, calls, payload

    def test_f1_external_candidate_reaches_ranked_offline(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "pool.json"
            cand_path = Path(tmp) / "candidates.magnets"
            out_path = Path(tmp) / "probe.json"
            code, _ = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--pool-file", str(pool_path),
                 "--json-out", str(Path(tmp) / "search.json"),
                 "--candidates-out", str(cand_path)],
                diao_payload([]), [self.external])
            self.assertEqual(code, 0)
            self.assertIn(magnet_for(self.external_hash),
                          cand_path.read_text(encoding="utf-8"))

            rc, calls, payload = self.run_probe(
                ["probe_magnets.py", "狂飙", "--kind", "movie", "--duration-minutes", "120",
                 "--candidate-file", str(cand_path), "--pool-file", str(pool_path),
                 "--json-out", str(out_path), "--no-live-check", "--workers", "2"])
            ranked = [item.get("ih") for item in payload["ranked"]]
            self.assertIn("fetch_metadata", calls,
                          "probe 没有为外部候选抓 metadata（池子里的 metadata_at 在撒谎）")
            self.assertIn(self.external_hash, ranked,
                          "外部候选没有进入 ranked（F1 的原始症状）")
            self.assertNotEqual(rc, 4, "probe 以 rc=4（没有候选）结束")
            self.assertEqual(rc, 0)
            self.assertTrue(payload["best"], "ranked 有候选却没有 best")
            self.assertEqual(payload["best"]["ih"], self.external_hash)


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


class DependencyTests(unittest.TestCase):
    """The backend stays zero-dependency."""
    def test_backend_modules_import_stdlib_only(self):
        allowed = set(sys.stdlib_module_names) | {"media_download_lib",
                                                  "torrents_csv_source",
                                                  "search_pool",
                                                  "source_config"}
        for name in ("search_pool.py", "torrents_csv_source.py", "source_config.py"):
            tree = ast.parse((BACKEND / name).read_text(encoding="utf-8"))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
            self.assertLessEqual(roots, allowed, f"{name} imports outside the stdlib: "
                                                 f"{sorted(roots - allowed)}")


if __name__ == "__main__":
    unittest.main()
