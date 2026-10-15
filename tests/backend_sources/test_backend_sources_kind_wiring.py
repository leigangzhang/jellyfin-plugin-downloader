"""把请求的 `kind` 接到关键字选择处：请求 → build_search_cmd → search_pool → load_keywords。

Subject: `backend/console_server.py` 的 `build_search_cmd`、
`backend/search/search_pool.py` 的 `--kind` 与 `collect` 兜底、
`backend/search/search_pool_round.py` 的 `load_sources(kind=…)`。

实测背景（t23 的统计，见 /tmp/jmd-test/evidence/FIX-P1a/README.md §2）：剧集发布组的
三条署名在电影/纪录片片名上只覆盖 1–4/8，`P1a` 因此加了 `keywords_for(kind)`。但**接口
做好不等于生效**：本轮之前 `search_pool_round.py:64` 是 `load_keywords()`（不传 kind）、
`search_pool.py` 没有 `--kind`、`build_search_cmd` 也不传 ⇒ 换片源关键字这一步完全没接线，
静默回落成剧集三条（不是崩溃，只是「配了但没生效」这种看不见的状态）。

三条边界同时钉住：电影/纪录片拿到电影组、剧集仍是那三条署名、**不传 kind 时与修复前
逐字一致**（argv 与关键字两处都逐字比）。
"""

from __future__ import annotations

import argparse
import sys
import unittest
from unittest import mock

from tests.backend_sources._backend_sources_helpers import source_config, search_pool
from search import search_pool_round  # noqa: E402 -- BACKEND 由上一行放进 sys.path

import console_server  # noqa: E402 -- 同上：build_search_cmd 是 L1 搜索 argv 的构造处

SERIES_KEYWORDS = ("高清剧集网", "DDHDTV", "BBHDTV")


def search_params(**overrides):
    """一条最小可用的搜索参数（形状照 `http_api._api_search` 的 params）。"""
    params = {
        "title": "一战再战",
        "slug": "一战再战",
        "pool_file": "/tmp/pool/一战再战.json",
        "cand_file": "/tmp/一战再战.16.magnets",
        "year": "2025",
        "alt_title": [],
        "original_title": "",
        "season": None,
        "publisher_only": False,
    }
    params.update(overrides)
    return params


class SearchCommandKindTests(unittest.TestCase):
    """请求 → 子进程 argv：`--kind` 必须真的拼进去，不传时旧 argv 逐字不变。"""

    def test_kind_is_added_to_the_argv(self):
        cmd = console_server.build_search_cmd(search_params(kind="movie"))
        self.assertIn("--kind", cmd, "kind 没拼进 argv ⇒ 子进程永远拿不到类型")
        self.assertEqual(cmd[cmd.index("--kind") + 1], "movie")

    def test_missing_kind_keeps_the_old_argv_verbatim(self):
        # 向后兼容是逐字的：没有 kind 的请求，argv 必须与修复前一模一样
        self.assertEqual(
            console_server.build_search_cmd(search_params()),
            [
                "search/search_pool.py", "一战再战",
                "--pool-file", "/tmp/pool/一战再战.json",
                "--candidates-out", "/tmp/一战再战.16.magnets",
                "--json-out", "/tmp/一战再战_search.json",
                "--year", "2025",
            ],
        )

    def test_blank_kind_is_not_emitted(self):
        self.assertNotIn("--kind", console_server.build_search_cmd(search_params(kind="")))

    def test_kind_is_normalised_nowhere_here(self):
        """归一化在下游（`source_config.normalise_kind`）：这里原样传，不自己造规则。"""
        cmd = console_server.build_search_cmd(search_params(kind=" Movie "))
        self.assertEqual(cmd[cmd.index("--kind") + 1], " Movie ")


class KindFlagTests(unittest.TestCase):
    """`search_pool.py` 的 `--kind` 入口：默认空串，未知类型不拒绝。"""

    def parse(self, argv):
        with mock.patch.object(sys, "argv", argv):
            return search_pool.parse_args()

    def test_default_is_the_empty_string(self):
        self.assertEqual(self.parse(["search_pool.py", "一战再战"]).kind, "")

    def test_explicit_kind_is_carried(self):
        self.assertEqual(
            self.parse(["search_pool.py", "一战再战", "--kind", "movie"]).kind, "movie")

    def test_unknown_kind_is_accepted(self):
        # 归一化与回落由 source_config 负责；CLI 不该把未知类型判成用法错误
        self.assertEqual(self.parse(["search_pool.py", "x", "--kind", "podcast"]).kind, "podcast")


class LoadSourcesKindTests(unittest.TestCase):
    """`load_sources(kind=…)` 把类型原样转给 `source_config.load_keywords`。"""

    def test_the_kind_reaches_load_keywords(self):
        seen = []

        def fake(text=None, kind=None):
            seen.append(kind)
            return ([f"kw:{kind}"], [])

        with mock.patch.object(source_config, "load_keywords", fake):
            sites, keywords, code = search_pool_round.load_sources(kind="movie")
        self.assertEqual(seen, ["movie"], "load_sources 必须把 kind 传下去")
        self.assertEqual(keywords, ["kw:movie"])
        self.assertEqual(code, 0)

    def test_no_kind_falls_back_to_the_old_call(self):
        """不传 kind 时关键字与旧调用完全一致（旧调用就是 `load_keywords()`）。"""
        self.assertEqual(search_pool_round.load_sources()[1], source_config.load_keywords()[0])
        self.assertEqual(search_pool_round.load_sources(kind="")[1], source_config.load_keywords()[0])


class KeywordGroupTests(unittest.TestCase):
    """空配置下按类型取默认组：电影/纪录片电影组、剧集三条署名。"""

    def setUp(self):
        # 本组测的是「空配置 → 按 kind 取默认组」；显式配置优先，那是另一条路径
        patcher = mock.patch.dict(source_config.environ, {source_config.ENV_KEYWORDS: ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_movie_and_record_get_the_film_group(self):
        for kind in ("movie", "record"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    search_pool_round.load_sources(kind=kind)[1],
                    list(source_config.keywords_for(kind)),
                )
        self.assertEqual(
            search_pool_round.load_sources(kind="movie")[1],
            list(source_config.DEFAULT_FILM_ANCHOR_KEYWORDS),
        )

    def test_episode_keeps_the_three_series_signatures(self):
        self.assertEqual(
            search_pool_round.load_sources(kind="episode")[1], list(SERIES_KEYWORDS))
        self.assertEqual(
            search_pool_round.load_sources(kind="episode")[1],
            list(source_config.DEFAULT_ANCHOR_KEYWORDS),
        )

    def test_film_and_series_groups_are_actually_different(self):
        # 两组若相同，这条接线等于没改（t25 点出的坑）
        self.assertNotEqual(
            list(source_config.keywords_for("movie")), list(SERIES_KEYWORDS))


class MainWiringTests(unittest.TestCase):
    """`main()` 把解析出来的 kind 交给 load_sources —— 否则 `--kind` 只是个摆设。"""

    def test_main_hands_the_parsed_kind_over(self):
        seen = []

        def spy(kind=""):
            seen.append(kind)
            return None, None, 2  # 早退码：main 立刻返回，不跑 collect

        with mock.patch.object(search_pool, "load_sources", spy), \
             mock.patch.object(search_pool, "prepare_args", lambda ns, args: None), \
             mock.patch.object(search_pool, "parse_args",
                               lambda: argparse.Namespace(kind="movie")):
            rc = search_pool.main()
        self.assertEqual(seen, ["movie"])
        self.assertEqual(rc, 2)


class CollectFallbackTests(unittest.TestCase):
    """`collect()` 自己读关键字那条兜底路径同样要带 kind。"""

    def test_collect_fallback_carries_the_kind(self):
        with mock.patch.object(sys, "argv", ["search_pool.py", "一战再战", "--kind", "movie"]):
            args = search_pool.parse_args()
        seen = []

        class _Stop(Exception):
            """哨兵：在关键字那一行抛出，避免真的发网络请求。"""

        def fake(text=None, kind=None):
            seen.append(kind)
            raise _Stop()

        with mock.patch.object(search_pool, "load_pool", lambda path: {}), \
             mock.patch.object(source_config, "load_keywords", fake):
            with self.assertRaises(_Stop):
                search_pool.collect(args, sites=[], keywords=None)
        self.assertEqual(seen, ["movie"])


if __name__ == "__main__":
    unittest.main()
