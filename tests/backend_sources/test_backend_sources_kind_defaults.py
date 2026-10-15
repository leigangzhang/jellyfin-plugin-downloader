"""按条目类型取搜索缺省值：剧集仍单源，电影/纪录片/综艺走全阶梯。

Subject: `backend/source_config.py` 的 `publisher_only_default(kind)` 与
`keywords_for(kind)`。

实测依据（2026-10-15，本机直打两个站点接口、按第一页计数，8 个真实片名 =
电影 4（一战再战/哈利波特/孤注一掷/怪物）+ 纪录片 4（回到太空/里斯本丸沉没/
泰国洞穴救援/地球脉动：极境生存），原始表见
`/tmp/jmd-test/evidence/FIX-P1a/source-keyword-grid.json`）：

* 剧集侧的三个 anchor（高清剧集网/DDHDTV/BBHDTV）在这 8 个片名上只覆盖 4/8、2/8、1/8
  ——它们确实是**剧集发布组**的签名，电影/纪录片基本搜不到；
* 电影/纪录片侧 `高清影视之家` 与 `HDBTHD` 各覆盖 5/8，是同组里最高的两个；
* 裸片名档覆盖 7/8 —— 所以对电影/纪录片来说，「按类型关掉单源」比换关键字更关键，
  两条都必须成立才拿得到候选。

这三条边界要同时钉住：剧集保持单源（旧行为）、非剧集不再单源、未识别的 kind 回落到
修复前的行为（向后兼容）。
"""

from __future__ import annotations

import unittest

from tests.backend_sources._backend_sources_helpers import source_config

FILM_KINDS = ("movie", "record", "variety")


class PublisherOnlyDefaultTests(unittest.TestCase):
    """`publisher_only_default(kind)`：只有剧集继续单源。"""

    def test_series_keeps_single_source(self):
        self.assertIs(source_config.publisher_only_default("episode"), True,
                      "剧集必须保持单源（修复前的行为不能被动掉）")

    def test_non_series_kinds_leave_single_source(self):
        for kind in FILM_KINDS:
            with self.subTest(kind=kind):
                self.assertIs(source_config.publisher_only_default(kind), False,
                              f"{kind} 默认必须走全阶梯；单源会把阶梯裁到只剩剧集签名档")

    def test_unknown_kind_keeps_the_old_behaviour(self):
        for kind in ("", None, "podcast", "  "):
            with self.subTest(kind=kind):
                self.assertIs(source_config.publisher_only_default(kind), True,
                              "未识别的 kind 回落修复前的单源默认，不擅自放行")

    def test_kind_is_normalised_before_the_lookup(self):
        for spelling in (" Movie ", "MOVIE", "record\n"):
            with self.subTest(spelling=spelling):
                self.assertIs(source_config.publisher_only_default(spelling), False)
        self.assertIs(source_config.publisher_only_default(" Episode "), True)


class KeywordsForTests(unittest.TestCase):
    """`keywords_for(kind)`：电影/纪录片拿到自己那组署名，剧集维持原样。"""

    def test_series_uses_the_series_anchor_keywords(self):
        for kind in ("episode", "variety"):
            with self.subTest(kind=kind):
                self.assertEqual(source_config.keywords_for(kind),
                                 tuple(source_config.DEFAULT_ANCHOR_KEYWORDS))

    def test_film_kinds_use_the_film_anchor_keywords(self):
        for kind in ("movie", "record"):
            with self.subTest(kind=kind):
                self.assertEqual(source_config.keywords_for(kind),
                                 tuple(source_config.DEFAULT_FILM_ANCHOR_KEYWORDS))

    def test_the_two_groups_do_not_overlap(self):
        """两组必须真的不同：相同就等于纪录片路径没有改动（队长 t25 点出的坑）。"""
        film = set(source_config.DEFAULT_FILM_ANCHOR_KEYWORDS)
        series = set(source_config.DEFAULT_ANCHOR_KEYWORDS)
        self.assertTrue(film)
        self.assertFalse(film & series,
                         f"电影/纪录片组与剧集组重叠：{sorted(film & series)}")

    def test_unknown_kind_falls_back_to_the_series_group(self):
        for kind in ("", None, "podcast"):
            with self.subTest(kind=kind):
                self.assertEqual(source_config.keywords_for(kind),
                                 tuple(source_config.DEFAULT_ANCHOR_KEYWORDS))

    def test_groups_respect_the_keyword_limits(self):
        for name in ("DEFAULT_ANCHOR_KEYWORDS", "DEFAULT_FILM_ANCHOR_KEYWORDS"):
            group = getattr(source_config, name)
            with self.subTest(group=name):
                self.assertLessEqual(len(group), source_config.MAX_KEYWORDS)
                self.assertEqual(len(set(group)), len(group), "组内不能有重复项")
                for keyword in group:
                    # 该索引对一行里的多个词是 AND 语义（2026-10-14 实测）：多词组合
                    # 只要有一个词不在索引里，整行 0 命中。默认值必须一条一个词。
                    self.assertNotIn(" ", keyword, f"{name} 里的 {keyword!r} 是多词组合")


class LoadKeywordsKindTests(unittest.TestCase):
    """空配置时由 kind 决定落到哪一组；不传 kind 时行为与修复前一致。"""

    def test_empty_config_takes_the_kind_group(self):
        self.assertEqual(source_config.load_keywords("", kind="movie"),
                         (list(source_config.DEFAULT_FILM_ANCHOR_KEYWORDS), []))
        self.assertEqual(source_config.load_keywords("", kind="record"),
                         (list(source_config.DEFAULT_FILM_ANCHOR_KEYWORDS), []))
        self.assertEqual(source_config.load_keywords("", kind="episode"),
                         (list(source_config.DEFAULT_ANCHOR_KEYWORDS), []))

    def test_no_kind_keeps_the_old_default(self):
        self.assertEqual(source_config.load_keywords(""),
                         (list(source_config.DEFAULT_ANCHOR_KEYWORDS), []))

    def test_explicit_config_still_wins(self):
        self.assertEqual(
            source_config.load_keywords('["甲", "乙"]', kind="movie"),
            (["甲", "乙"], []),
            "用户配了关键字就以配置为准，按类型的默认只是兜底")


if __name__ == "__main__":
    unittest.main()
