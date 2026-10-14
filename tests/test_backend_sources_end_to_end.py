"""`main()` over both channels with the HTTP boundary replaced.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import json
import unittest
import urllib.parse
from pathlib import Path

from tests._backend_sources_helpers import (
    DIAO_SITE,
    FakeHttp,
    canned_pages,
    diao_payload,
    l1_torrent,
    load_fixture,
    run_search_main,
    search_pool,
    sites_env,
    source_config,
    torrents_csv_source,
)

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

    def test_a_cloud_only_station_is_never_asked_for_magnets(self):
        """云盘专用站（只有网页搜索地址）不参与磁力取源，也不占预算切片。"""
        import tempfile

        api = FakeHttp(canned_pages("漫长的季节"))
        calls = []
        sites = [
            dict(DIAO_SITE),
            {"name": "搜索页", "kind": "", "base_url": "",
             "web_search_url": "https://s.example/search?q={q}"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, lambda *a, **k: calls.append(a) or {"status": 200, "entries": []},
                env={source_config.ENV_SITES: sites_env(sites)})
            self.assertEqual(code, 0)
            self.assertEqual(calls, [], "云盘专用站不该被磁力通道问到")
            self.assertNotIn("EXTRA_SOURCE", printed)

    def test_only_cloud_stations_means_no_magnet_source(self):
        """一个磁力入口都没有时说清楚，并把候选为空报成 rc=4，而不是假装搜过。"""
        import tempfile

        api = FakeHttp(canned_pages("漫长的季节"))
        sites = [{"name": "搜索页", "kind": "", "base_url": "",
                  "web_search_url": "https://s.example/search?q={q}"}]
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = self.run_main(
                ["search_pool.py", "漫长的季节", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                api, lambda *a, **k: {"status": 200, "entries": []},
                env={source_config.ENV_SITES: sites_env(sites)})
            self.assertEqual(code, 4)
            self.assertIn("NO_MAGNET_STATION", printed)
            self.assertEqual(api.requests, [], "没有磁力入口时不该发任何请求")

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
