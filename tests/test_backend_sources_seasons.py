"""Season recognition and its two consumers: the explicit forms, the single home
shared with `build_console`, and the strict-season cut-off.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests._backend_sources_helpers import (
    FakeResponse,
    diao_payload,
    l1_torrent,
    run_main_with_fakes,
    search_pool,
    torrents_csv_source,
)

class SeasonDetectionTests(unittest.TestCase):
    """季号识别的三种显式写法都要认，且只认显式写法（不猜）。

    只认 `Sxx` 会把中文资源站的「第二季」整批漏成「没表态」，严格按季就收不住它们；
    反过来把 `全集`/`第11集` 也当成季号会误杀。两个方向都在这里钉住。
    """

    def seasons(self, name):
        return search_pool.candidate_seasons({"name": name})

    def test_explicit_forms_are_recognised(self):
        cases = {
            "Show.S01E05.1080p": {1},
            "Show.S02.2160p": {2},
            "Show.第2季[第01-03集].2160p": {2},
            "Show.第二季[第01-03集].2160p": {2},
            "Show.Season 3.1080p.WEB-DL": {3},
            "Show.第十二季.S12.1080p": {12},
            "本周更新.第二十季.1080p": {20},
        }
        for name, expect in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.seasons(name), expect)

    def test_a_name_without_a_season_token_says_nothing(self):
        for name in ("Show[全集][简繁].1080p", "Show[全季][简繁].1080p",
                     "Show[第11集][中文字幕]", "Show[全12集][国语].2160p"):
            with self.subTest(name=name):
                self.assertEqual(self.seasons(name), set(), "没表态的名字不该被猜出季号")

    def test_conflicting_evidence_keeps_both_seasons(self):
        """名字里既有「第二季」又有 `S01`：并集，宁可留着也不因为冲突丢掉。"""
        self.assertEqual(self.seasons("Show.第二季.S01.1080p"), {1, 2})

    def test_chinese_numerals_parse_up_to_ninety_nine(self):
        self.assertEqual(search_pool.cn_number("十"), 10)
        self.assertEqual(search_pool.cn_number("十一"), 11)
        self.assertEqual(search_pool.cn_number("二十"), 20)
        self.assertEqual(search_pool.cn_number("二十一"), 21)
        self.assertIsNone(search_pool.cn_number("百"))
        self.assertIsNone(search_pool.cn_number(""))
        self.assertIsNone(search_pool.cn_number("一二三四"))


class SeasonRecognitionHasOneHome(unittest.TestCase):
    """季号识别只有一份实现：面板的「该季」折叠与后端的严格按季收口必须同源。

    复核第一轮之后我在 `search_pool` 里新写了一份，而 `build_console` 早就有一份
    （`SEASON_CODE_RE` / `SEASON_CN_RE` / `SEASON_EN_RE` / `_cn_number`）——两份一旦漂移，
    同一个名字在面板与后端会得到不同的季号结论。现在 `build_console` 只做「排序后返回」。
    """

    CORPUS = (
        "Show.S01E05.1080p",
        "Show.S02.2160p",
        "Show.第二季[第01-03集].2160p",
        "Show.第2季[第01-03集].2160p",
        "Show.Season 3.1080p.WEB-DL",
        "Show.第十二季.S12.1080p",
        "Show[全集][简繁].1080p",
        "Show[全季][简繁].1080p",
        "Show[第11集][中文字幕]",
        "Show.第二季.S01.1080p",
    )

    def test_build_console_returns_the_same_numbers_as_search_pool(self):
        import build_console

        for name in self.CORPUS:
            with self.subTest(name=name):
                candidate = {"name": name, "videos": [{"path": "a.mkv"}]}
                self.assertEqual(
                    build_console.candidate_seasons(candidate),
                    sorted(search_pool.candidate_seasons(candidate)),
                )

    def test_build_console_keeps_no_second_copy_of_the_regexes(self):
        import build_console

        for stale in ("SEASON_CODE_RE", "SEASON_CN_RE", "SEASON_EN_RE",
                      "CN_DIGITS", "_cn_number"):
            self.assertFalse(
                hasattr(build_console, stale),
                f"{stale} 是第二份季号识别；它只该留在 search_pool",
            )


class StrictSeasonFilterTests(unittest.TestCase):
    """严格按季的收口：锚点档带回来的「别的季」必须在这一层被丢掉（复核报告 F4）。

    锚点档（`片名 + 发布者署名`）不带季标记，会把同一部剧的其它季一起带回来——这是它换来
    召回的代价。898bcf8 的 strict 分支会在发布者档之前 return，所以那时不存在这个泄漏；
    现在锚点档也在 strict 下运行，就必须有显式季标记的收口，否则「严格按季」（默认开）
    等于失效。确定不了季号的候选（整包 / 裸片名 / 第N集）留着，不猜。
    """

    FILES = [{"index": 0, "path": "a.mkv", "size": 4 * 1024 ** 3}]

    def payload(self):
        return diao_payload(
            [
                l1_torrent("A" * 40, "【高清剧集网发布 www.DDHDTV.com】周三俱乐部[全12集]"
                                     ".Wednesday.Club.S01.1080p", files=list(self.FILES)),
                l1_torrent("B" * 40, "【高清剧集网发布 www.DDHDTV.com】星期三.第二季[第01-03集]"
                                     ".Wednesday.S02.2160p", files=list(self.FILES)),
                l1_torrent("C" * 40, "周三俱乐部[全12集][简繁英字幕].1080p",
                           files=list(self.FILES)),
            ],
            has_more=False,
        )

    def run_search(self, extra):
        import tempfile

        page = self.payload()
        with tempfile.TemporaryDirectory() as tmp:
            json_out = Path(tmp) / "out.json"
            code, printed = run_main_with_fakes(
                ["search_pool.py", "周三俱乐部", "--no-pool", "--sleep", "0",
                 "--season", "1", "--collection-hint", "第1季",
                 "--json-out", str(json_out),
                 "--candidates-out", str(Path(tmp) / "c.magnets"),
                 *extra],
                page, [],
            )
            self.assertEqual(code, 0, printed)
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            hashes = {item["ih"] for item in payload["candidates"]}
        return hashes, printed

    def test_strict_mode_drops_the_other_season_and_keeps_the_silent_ones(self):
        hashes, printed = self.run_search(["--strict-match"])
        self.assertIn("STRICT_SEASON_FILTER", printed)
        self.assertNotIn("B" * 40, hashes, "明确标了 S02 的候选不该出现在按 S01 的搜索里")
        self.assertIn("A" * 40, hashes, "S01 的候选必须留着")
        self.assertIn("C" * 40, hashes, "没标季号的候选留着，不猜")

    def test_strict_mode_drops_a_chinese_numeral_other_season(self):
        """「第二季」这种不带 Sxx 的写法同样要被收口（复核要点里我自己点出的缺口）。"""
        page = diao_payload(
            [
                l1_torrent("A" * 40, "【高清剧集网发布 www.DDHDTV.com】周三俱乐部[全12集]"
                                     ".Wednesday.Club.S01.1080p", files=list(self.FILES)),
                l1_torrent("D" * 40, "【高清剧集网发布 www.DDHDTV.com】星期三.第二季[第01-03集]"
                                     ".2160p", files=list(self.FILES)),
            ],
            has_more=False,
        )
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            json_out = Path(tmp) / "out.json"
            code, printed = run_main_with_fakes(
                ["search_pool.py", "周三俱乐部", "--no-pool", "--sleep", "0",
                 "--season", "1", "--collection-hint", "第1季", "--strict-match",
                 "--json-out", str(json_out),
                 "--candidates-out", str(Path(tmp) / "c.magnets")],
                page, [],
            )
            self.assertEqual(code, 0, printed)
            hashes = {item["ih"] for item in json.loads(
                json_out.read_text(encoding="utf-8"))["candidates"]}
        self.assertNotIn("D" * 40, hashes, "中文「第二季」的候选不该出现在按 S01 的搜索里")
        self.assertIn("A" * 40, hashes)

    def test_a_killed_round_lands_already_filtered_candidates(self):
        """中途被杀（等价于控制台 600s 硬杀 rc=124）时落盘的那份也必须已收口。

        复核第二轮 F2：过滤原来只在 `main()` 末尾跑，而 `collect` 每完成一个关键词就落一次
        盘 —— 被杀那一轮留下的未过滤文件照样会被交出去。现在收口在落盘路径上。
        """
        import tempfile

        page = diao_payload(
            [
                l1_torrent("A" * 40, "周三俱乐部[全12集].Wednesday.Club.S01.1080p",
                           files=list(self.FILES)),
                l1_torrent("B" * 40, "星期三.第二季[第01-03集].2160p", files=list(self.FILES)),
            ],
            has_more=False,
        )

        landed_at_kill = {}

        class KillOnSecondQuery:
            """第一个关键词正常返回，第二个模拟「进程被杀」。

            被杀那一刻先把磁盘上的候选文件读下来：真实的 SIGKILL 不会跑 `finally`，
            所以那份文件就是控制台接下来会喂给 probe 的东西。
            """

            def __init__(self, payload, path):
                self.payload = payload
                self.path = path
                self.calls = 0

            def __call__(self, request, timeout=None):
                self.calls += 1
                if self.calls > 1:
                    landed_at_kill["text"] = self.path.read_text(encoding="utf-8")
                    raise KeyboardInterrupt("simulated rc=124")
                return FakeResponse(json.dumps(self.payload).encode("utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp) / "c.magnets"
            argv = ["search_pool.py", "周三俱乐部", "--no-pool", "--sleep", "0",
                    "--season", "1", "--collection-hint", "第1季", "--strict-match",
                    # 并发抓页会让「第几次请求」变成波次而不是关键词：这里改成串行，
                    # 好让第二个**关键词**（而不是同一波里的第二页）成为被杀的那一个
                    "--page-workers", "1",
                    "--json-out", str(Path(tmp) / "out.json"),
                    "--candidates-out", str(cand)]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(search_pool.urllib.request, "urlopen",
                                      KillOnSecondQuery(page, cand)), \
                    mock.patch.object(torrents_csv_source, "search",
                                      lambda *a, **k: {"status": 200, "entries": []}), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    search_pool.main()
        self.assertTrue(landed_at_kill, "第二个关键词应当触发模拟的杀进程")
        self.assertIn("A" * 40, landed_at_kill["text"], "第一个关键词的候选要留下")
        self.assertNotIn("B" * 40, landed_at_kill["text"],
                         "被杀那一刻磁盘上的候选也必须已经过严格按季收口")

    def test_without_strict_mode_nothing_is_dropped(self):
        hashes, _ = self.run_search([])
        self.assertIn("B" * 40, hashes, "非严格模式不做季过滤")
