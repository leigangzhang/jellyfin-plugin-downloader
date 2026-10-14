"""The keyword ladder: the anchor tier leads, aliases/year/title follow it.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from tests._backend_sources_helpers import search_pool

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
