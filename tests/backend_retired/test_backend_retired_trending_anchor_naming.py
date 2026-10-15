"""「热门」关注链路的源码守卫：占位命名（A7）、零写入（A9）、key 不回显与配置往返。

Subject: `Jellyfin.Plugin.JellyfinDownloader/Controllers/JellyfinDownloaderController.cs`
的占位命名与两条 trending 端点、`Configuration/PluginConfiguration.cs` 的默认值、
`Configuration/configPage.html` 的「热门」分组、`BackendManager.cs` 的 key 注入。

这一族断言都只能读源码：它们的效果要起 Jellyfin、装上插件、真的点一次「关注」才看得见，
而单层 worktree 里没有那条链路。所以把不变量钉在源文本上——每条都能在有人改坏它时变红。

**A7（占位与归档不许撞名）**是这里最重要的一条：插件「关注」时会在媒体库里写一个占位
mp4（AnchorEnabled 打开时），目录刻意与下载归档的落点相同；后端归档真文件时用的是
`backend/media_download_lib/paths.py` 的 `normalized_destination`。两边落点一旦相同，
归档会把真文件写进占位（或占位顶掉归档）——两种都是静默的数据损失，而两侧各自的测试都
看不见。区分只靠占位文件名里的 `- zz placeholder` 标签。

判定不是「模板长得不一样」这种字面断言：模板、标签、清洗字符类都从 C# 源码里**按名字读出来**
（读不到、出现不认识的标识符都直接失败，守卫不许因为解析不到而静默变绿），再对一组语料
逐条与 Python 侧**真实执行的** `normalized_destination` 对照。
"""

from __future__ import annotations

import os
import posixpath
import re
import unittest

from tests.backend_retired._backend_retired_helpers import (
    BACKEND,
    ROOT,
    anonymous_object_keys,
)

PLUGIN_DIR = ROOT / "Jellyfin.Plugin.JellyfinDownloader"
CONTROLLER = PLUGIN_DIR / "Controllers" / "JellyfinDownloaderController.cs"
PLUGIN_CONFIG = PLUGIN_DIR / "Configuration" / "PluginConfiguration.cs"
CONFIG_PAGE = PLUGIN_DIR / "Configuration" / "configPage.html"
BACKEND_MANAGER = PLUGIN_DIR / "BackendManager.cs"

# 语料：含空格、中文、同年同名不同年、片名带 ".mp4"、片名含 ":" 与 "/"、连续空白、纯数字片名。
# 覆盖的正是「归一化/清洗会吃掉什么」的边界——占位与归档的落点在这些边界上最容易撞。
CORPUS = (
    ("热辣滚烫", "2024"),
    ("The Matrix", "1999"),
    ("The Matrix", "2024"),
    ("Dark Glory S2", "2023"),
    ("片名.mp4", "2021"),
    ("Face/Off", "1997"),
    ("Se7en: Part 2", "2024"),
    ("A  B   C", ""),
    ("1917", "2019"),
    ("沙丘：第二部", "2024"),
)

PATH_SEPARATORS = "/\\"
WINDOWS_RESERVED = ':*?"<>|'

# 探测用的媒体根：只用来把两边算成「相对媒体根的路径」，不需要真的存在。
PROBE_MEDIA_ROOT = "/tmp/trending-anchor-probe-media-root"

# C# 声明：`private const string X = "…";` / `private static readonly string X = "…" + Y + Z;`
_CS_STRING_DECL = r'(?:const|static\s+readonly)\s+string\s+{name}\s*=\s*(?P<expr>[^;]+);'
# 模板表达式只允许「字符串字面量」与「已知常量名」用 + 连接。
_CS_EXPR_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([A-Za-z_][A-Za-z0-9_]*)')

TEMPLATE_NAMES = (
    "MovieRelativeTemplate",
    "SeriesRelativeTemplate",
    "RecordRelativeTemplate",
)
LITERAL_NAMES = (
    "PlaceholderLabel",
    "PlaceholderExtension",
    "YearSegmentTemplate",
    "ForbiddenTitleChars",
    "WhitespaceRunPattern",
    "TitleCleanPattern",
    "FourDigitYearPattern",
)


def _controller_source() -> str:
    return CONTROLLER.read_text(encoding="utf-8")


def _decl_expression(source: str, name: str) -> str:
    """按名字取 C# 里的字符串声明表达式（恰好一处，否则直接失败）。"""
    pattern = re.compile(_CS_STRING_DECL.format(name=re.escape(name)), re.S)
    found = pattern.findall(source)
    if len(found) != 1:
        raise AssertionError(
            f"controller 里 {name} 期望恰好 1 处字符串声明，实际 {len(found)} 处"
            "（改名字/改成别的形态时必须同时更新本守卫，守卫不许因此变绿）")
    return found[0]


def _unescape(literal: str) -> str:
    out = []
    index = 0
    while index < len(literal):
        char = literal[index]
        if char == "\\" and index + 1 < len(literal):
            out.append(literal[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _evaluate_expression(expression: str, known: dict[str, str], subject: str) -> str:
    """把 `"a" + Known + "b"` 折成字符串；出现不认识的标识符就失败。"""
    parts: list[str] = []
    consumed = 0
    for match in _CS_EXPR_TOKEN.finditer(expression):
        gap = expression[consumed:match.start()]
        if re.sub(r"[\s+]", "", gap):
            raise AssertionError(f"{subject}：表达式里有守卫看不懂的片段 {gap!r}")
        consumed = match.end()
        if match.group(1) is not None:
            parts.append(_unescape(match.group(1)))
            continue
        name = match.group(2)
        if name not in known:
            raise AssertionError(
                f"{subject}：表达式引用了守卫不认识的标识符 {name!r}（{expression!r}）"
                "——常量被改名/换形态时要同步更新守卫，不能靠读不到而静默变绿")
        parts.append(known[name])
    if re.sub(r"[\s+]", "", expression[consumed:]):
        raise AssertionError(f"{subject}：表达式尾部有守卫看不懂的内容 {expression[consumed:]!r}")
    return "".join(parts)


def literal_constants() -> dict[str, str]:
    """C# 里的字面常量（标签、后缀、清洗字符类、空白正则、年份正则）。"""
    source = _controller_source()
    return {
        name: _evaluate_expression(_decl_expression(source, name), {}, name)
        for name in LITERAL_NAMES
    }


def anchor_templates() -> dict[str, str]:
    """四个命名模板的**实际取值**（含标签常量的拼接结果）。"""
    source = _controller_source()
    known = literal_constants()
    return {
        name: _evaluate_expression(_decl_expression(source, name), known, name)
        for name in TEMPLATE_NAMES
    }


def anchor_relative_path(template: str, safe_title: str, year: str, year_segment_template: str) -> str:
    """用 C# 里的模板（与年份段模板）算出占位文件的相对路径。"""
    segment = year_segment_template.format(year) if year else ""
    return template.format(safe_title, segment)


def clean_title(raw: str, forbidden: str, whitespace_pattern: str) -> str:
    """用 C# 里**读出来的**字符类与空白正则复算清洗结果（规则本身来自源码）。"""
    stripped = "".join(char for char in raw if char not in forbidden)
    return re.sub(whitespace_pattern, " ", stripped).strip()


def _method_block(source: str, signature: str, end_marker: str) -> str:
    start = source.index(signature)
    return source[start:source.index(end_marker, start)]


def _normalized_destination():
    """后端真实执行的落点函数（不是抄一份规则，是 import 真的那个）。"""
    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from media_download_lib.paths import normalized_destination  # noqa: PLC0415

    return normalized_destination


class AnchorNamingNeverCollidesWithTheBackendLanding(unittest.TestCase):
    """A7：占位落点与后端归档落点对同一 (片名, 年份, 类别) 永不相等。"""

    def test_templates_and_cleaning_are_read_from_the_real_source(self) -> None:
        """守卫自己的前提：模板/常量真的读到了，且被占位函数真用上。"""
        templates = anchor_templates()
        self.assertEqual(sorted(templates), sorted(TEMPLATE_NAMES))

        body = _method_block(
            _controller_source(),
            "private static string PlaceholderRelativePath(",
            "private static bool IsWithinRoot(")
        for name in TEMPLATE_NAMES:
            self.assertIn(name, body, f"占位命名函数没有用上模板 {name}")

        clean_body = _method_block(
            _controller_source(),
            "private static string CleanTitle(",
            "private static string PlaceholderRelativePath(")
        self.assertIn("ForbiddenTitleChars", clean_body, "清洗必须用那个字符类常量")
        self.assertIn("WhitespaceRunRegex", clean_body, "清洗必须折叠连续空白")
        self.assertIn(".Trim()", clean_body, "清洗必须去掉首尾空白")

        # 标签必须真的出现在每个模板里：它就是防撞名的唯一保证。
        label = literal_constants()["PlaceholderLabel"]
        self.assertTrue(label.strip(), "标签常量是空的，防撞名就没有保证了")
        for name, value in templates.items():
            self.assertIn(label, value, f"模板 {name} 里没有标签 {label!r}")

    def test_anchor_relative_path_never_equals_the_backend_landing(self) -> None:
        templates = anchor_templates()
        constants = literal_constants()
        forbidden = constants["ForbiddenTitleChars"]
        whitespace = constants["WhitespaceRunPattern"]
        normalized_destination = _normalized_destination()
        label = constants["PlaceholderLabel"]
        extension = constants["PlaceholderExtension"]

        for title, year in CORPUS:
            safe = clean_title(title, forbidden, whitespace)
            self.assertTrue(safe, f"语料 {title!r} 清洗后为空，用例失去意义")
            folder = f"{safe} ({year})" if year else safe
            # 两侧都用**清洗后的**片名：这才是最容易撞名的情形（两侧只差标签）。
            for kind in ("movie", "series", "record"):
                if kind == "movie":
                    template = templates["MovieRelativeTemplate"]
                    source_path = f"/incoming/{safe}.mp4"
                    media_kind = "movie"
                    landing_dirs = ("Movies", posixpath.join("Movies", folder))
                elif kind == "series":
                    template = templates["SeriesRelativeTemplate"]
                    source_path = f"/incoming/{safe} S01E01.mp4"
                    media_kind = "tv"
                    landing_dirs = ("TV Shows", posixpath.join("TV Shows", folder))
                else:
                    # 纪录片：后端落点是「一个目录 + 一部片」，与电影同形，分类目录是 Records
                    template = templates["RecordRelativeTemplate"]
                    source_path = f"/incoming/{safe}.mp4"
                    media_kind = "movie"
                    landing_dirs = ("Records", posixpath.join("Records", folder))

                anchor_relative = anchor_relative_path(
                    template, safe, year, constants["YearSegmentTemplate"])
                # 形状自检：占位文件名必须带标签 + 扩展名，否则下面的「不相等」可能是巧合。
                self.assertTrue(
                    posixpath.basename(anchor_relative).endswith(label + extension),
                    f"占位落点没有以 {label + extension!r} 结尾：{anchor_relative!r}")

                for landing_dir in landing_dirs:
                    landing = normalized_destination(
                        source_path,
                        os.path.join(PROBE_MEDIA_ROOT, landing_dir),
                        media_kind=media_kind,
                        series_name=safe,
                        year=year)
                    landing_relative = os.path.relpath(landing, PROBE_MEDIA_ROOT)
                    self.assertNotEqual(
                        posixpath.normpath(anchor_relative),
                        posixpath.normpath(landing_relative),
                        f"占位与归档撞名了（{kind} {title!r} {year!r}）："
                        f"anchor={anchor_relative!r} landing={landing_relative!r}")
                    # 归档文件名里不许出现标签：出现就说明两侧命名被抄成了一份。
                    self.assertNotIn(label, posixpath.basename(landing_relative))

    def test_the_label_is_what_keeps_the_two_apart(self) -> None:
        """同目录下的「只差标签」对照：证明区分确实来自标签，不是来自目录差异。

        把后端落点算到与占位**同一个目录**里（movie: Movies/<safe> (<year>)，
        tv: TV Shows/<safe> (<year>)/Season 01），文件名仍必须不同——这就是 A7 的
        正面证明：标签一丢，这条断言立刻红。
        """
        templates = anchor_templates()
        constants = literal_constants()
        normalized_destination = _normalized_destination()
        label = constants["PlaceholderLabel"]

        for title, year in CORPUS:
            safe = clean_title(title, constants["ForbiddenTitleChars"], constants["WhitespaceRunPattern"])
            folder = f"{safe} ({year})" if year else safe
            for kind in ("movie", "series", "record"):
                if kind == "movie":
                    template = templates["MovieRelativeTemplate"]
                    final_dir = posixpath.join("Movies", folder)
                    source_path = f"/incoming/{safe}.mp4"
                    media_kind = "movie"
                elif kind == "series":
                    template = templates["SeriesRelativeTemplate"]
                    # tv 的最终目录是「剧集目录」（normalized_destination 自己再拼 Season NN）
                    final_dir = posixpath.join("TV Shows", folder)
                    source_path = f"/incoming/{safe} S01E01.mp4"
                    media_kind = "tv"
                else:
                    template = templates["RecordRelativeTemplate"]
                    final_dir = posixpath.join("Records", folder)
                    source_path = f"/incoming/{safe}.mp4"
                    media_kind = "movie"

                anchor_relative = anchor_relative_path(
                    template, safe, year, constants["YearSegmentTemplate"])
                landing = normalized_destination(
                    source_path,
                    os.path.join(PROBE_MEDIA_ROOT, final_dir),
                    media_kind=media_kind,
                    series_name=safe,
                    year=year)

                landing_relative = os.path.relpath(landing, PROBE_MEDIA_ROOT)
                self.assertEqual(
                    posixpath.dirname(landing_relative),
                    posixpath.dirname(posixpath.normpath(anchor_relative)),
                    f"{kind} {title!r}：这条对照要求两边同目录，语料/模板变了就更新用例")
                anchor_name = posixpath.basename(anchor_relative)
                landing_name = posixpath.basename(landing_relative)
                self.assertNotEqual(
                    anchor_name, landing_name,
                    f"同目录下占位与归档同名了（{kind} {title!r} {year!r}）：标签没有起作用")
                # 差距就是标签本身：去掉标签后两条名字应当相同（保证「标签是唯一差异」）
                self.assertEqual(
                    anchor_name.replace(label, ""), landing_name,
                    f"{kind} {title!r}：占位与归档的差异不止是标签，用例的前提被推翻了")

    def test_the_year_segment_placement_matches_the_naming_rule(self) -> None:
        """年份段 `{1}` 的落点必须与命名规则一致，否则「省略年份」会改错地方。

        movie：目录与文件名**都**带年份（`{1}` 两处）——它俩共用一个年份段，
        所以年份缺失时同步省略；series：按后端惯例文件名到 `S01E01` 为止，
        年份只出现在目录里（`{1}` 一处）。数目对不上就说明模板被改成了别的形状。
        """
        templates = anchor_templates()
        self.assertEqual(literal_constants()["YearSegmentTemplate"].count("{0}"), 1,
                         "年份段模板必须只有一个年份占位符")
        self.assertEqual(templates["MovieRelativeTemplate"].count("{1}"), 2,
                         "movie 模板里 {1} 的个数不是 2：目录与文件名没有共用同一个年份段")
        self.assertEqual(templates["SeriesRelativeTemplate"].count("{1}"), 1,
                         "series 模板里 {1} 的个数不是 1：年份只该出现在剧集目录里")
        self.assertEqual(templates["RecordRelativeTemplate"].count("{1}"), 2,
                         "record 模板里 {1} 的个数不是 2：纪录片与电影同形，目录与文件名共用年份段")

    def test_cleaning_strips_path_separators_and_reserved_characters(self) -> None:
        constants = literal_constants()
        forbidden = constants["ForbiddenTitleChars"]
        for char in PATH_SEPARATORS + WINDOWS_RESERVED:
            self.assertIn(char, forbidden, f"清洗字符类漏了 {char!r}")
        # 反斜杠在 C# 源码里是转义写法，读出来必须还是那一个字符
        self.assertEqual(len(forbidden), len(set(forbidden)), "清洗字符类里有重复字符")

        for title in ("Face/Off", "A\\B", "片名/../x", 'a:b*c?d"e<f>g|h', "  A   B  "):
            safe = clean_title(title, forbidden, constants["WhitespaceRunPattern"])
            for char in PATH_SEPARATORS + WINDOWS_RESERVED:
                self.assertNotIn(char, safe, f"{title!r} 清洗后仍含 {char!r}：{safe!r}")
            self.assertEqual(safe, safe.strip(), f"{title!r} 清洗后仍有首尾空白")
            self.assertNotIn("  ", safe, f"{title!r} 清洗后仍有连续空白")

    def test_the_normalizer_uses_the_same_character_class_as_check_exists(self) -> None:
        """查库归一化的字符类必须与 `backend/download/check_exists.py` 的 CLEAN_RE 一致。

        两处都回答「这个片名在库里吗」：字符类一旦分叉，同一个片名会在一处算命中、
        另一处算未命中，而单侧测试都看不出来。
        """
        check_exists = (BACKEND / "download" / "check_exists.py").read_text(encoding="utf-8")
        landed = re.search(r"CLEAN_RE\s*=\s*re\.compile\(r\"([^\"]+)\"\)", check_exists)
        self.assertIsNotNone(landed, "check_exists.py 里的 CLEAN_RE 形态变了，守卫要跟着更新")
        self.assertEqual(
            landed.group(1), literal_constants()["TitleCleanPattern"],
            "C# 的片名归一化字符类与 check_exists.CLEAN_RE 不一致")


class AnchorWritesNothingWhenDisabledOrAlreadyThere(unittest.TestCase):
    """A9（插件侧）：开关关闭与重复 anchor 都不写文件。"""

    def _anchor_action(self) -> str:
        return _method_block(
            _controller_source(),
            "public IActionResult TrendingAnchor(",
            "private static List<(string Title")

    def test_disabled_returns_before_any_write(self) -> None:
        anchor = self._anchor_action()
        disabled_at = anchor.index('reason = "disabled"')
        for later in ("RefreshMediaPaths()",
                      "ReadPlaceholderBytes()",
                      "System.IO.Directory.CreateDirectory",
                      "System.IO.File.WriteAllBytes",
                      "System.IO.File.Move"):
            self.assertLess(
                disabled_at, anchor.index(later),
                f"「{later}」排在了 disabled 分支之前：开关关着就可能写库了")

    def test_the_existing_file_is_returned_without_being_rewritten(self) -> None:
        anchor = self._anchor_action()
        exists_at = anchor.index("System.IO.File.Exists(finalPath)")
        write_at = anchor.index("System.IO.File.WriteAllBytes")
        move_at = anchor.index("System.IO.File.Move")
        self.assertLess(exists_at, write_at, "先判存在再写：顺序反了就会覆盖已落地的文件")
        self.assertLess(exists_at, move_at)
        between = anchor[exists_at:write_at]
        self.assertIn("exists = true", between, "已存在分支必须直接返回 exists=true，不重写")
        self.assertIn("return", between)


class TrendingSurfaceStaysConsistent(unittest.TestCase):
    """配置默认值、GET/POST config 的字段、白名单、分片登记、BackendManager 注入。"""

    def _config_payload_keys(self, signature: str, end_marker: str) -> list[str]:
        block = _method_block(_controller_source(), signature, end_marker)
        return [key.lower() for key in anonymous_object_keys(block)]

    def test_config_reports_configured_but_never_the_key_itself(self) -> None:
        """GET/POST config 只回 tmdbApiKeyConfigured，绝不下发 key 本身。"""
        for signature, end_marker in (
            ("public IActionResult GetConfig()", "public IActionResult UpdateConfig("),
            ("public IActionResult UpdateConfig(", "private static string LanguageOrDefault("),
        ):
            keys = self._config_payload_keys(signature, end_marker)
            self.assertIn("tmdbapikeyconfigured", keys,
                          f"{signature} 的响应里没有 tmdbApiKeyConfigured")
            self.assertNotIn("tmdbapikey", keys,
                             f"{signature} 的响应里出现了 key 本身，绝不允许下发")
        # 默认值与回落值同源：语言默认 zh-CN 只写一次
        plugin_config = PLUGIN_CONFIG.read_text(encoding="utf-8")
        self.assertIn('DefaultTmdbLanguage = "zh-CN"', plugin_config)
        self.assertIn("DefaultTmdbLanguage", _controller_source())

    def test_plugin_configuration_defaults_are_the_four_lists(self) -> None:
        plugin_config = PLUGIN_CONFIG.read_text(encoding="utf-8")
        self.assertEqual(
            _string_array(plugin_config, "DefaultTrendingLists"),
            ["trending_movie_week", "trending_tv_week", "popular_movie", "popular_tv"],
            "内置榜单默认值变了：配置页的四个复选框与后端的默认都指着它")
        for declaration in (
            "public string TmdbApiKey { get; set; } = string.Empty;",
            "public string TmdbLanguage { get; set; } = DefaultTmdbLanguage;",
            "public List<string> TrendingLists { get; set; } = new(DefaultTrendingLists);",
        ):
            self.assertIn(declaration, plugin_config, f"配置项默认值形态变了：{declaration}")
        self.assertRegex(plugin_config, r"public bool AnchorEnabled \{ get; set; \}")
        # AnchorEnabled 默认必须是 false（= 只记录关注，媒体库零写入）
        anchor_decl = re.search(r"public bool AnchorEnabled \{ get; set; \}([^;]*);", plugin_config)
        self.assertIsNotNone(anchor_decl)
        self.assertNotIn("true", anchor_decl.group(1), "占位开关的默认值被改成 true 了")

    def test_a_missing_year_is_not_an_invalid_batch_item(self) -> None:
        """查库请求里「没给年份」是合法的：年份缺失 = 无年份，不是非法项。

        这条是从一次真实的退化里长出来的（把年字段的缺失当成非法，整批被拒成
        {"items":[]}，海报墙上一片空白）。所以只认一种写法：**字段存在**且不是合法
        年份才算非法——判定里必须是 `&& !TryReadYearValue(...)`，写 `||` 就是反的。
        """
        body = _method_block(
            _controller_source(),
            "private static List<(string Title",
            "private static List<(string Kind")
        self.assertIn("int? year = null;", body, "年份要先按「无年份」初始化")
        year_lines = [line for line in body.splitlines() if '"year"' in line]
        self.assertEqual(len(year_lines), 1, "年份字段的判定应当只有一处：" + str(year_lines))
        self.assertIn("&& !TryReadYearValue", year_lines[0],
                      "年份判定写反了：字段缺失会被当成非法，整批回空清单")
        self.assertNotIn("||", year_lines[0], "年份判定里出现 ||：字段缺失会被当成非法")

    def test_the_anchor_requires_a_four_digit_year(self) -> None:
        """anchor 必须带 4 位年份：缺了会落到 <title>/，与真条目 <title> (<year>)/ 永不相等。

        无年份的落点必然在库里多造一个重复条目（提案要防的库污染），所以缺年份是 400，
        而且要在**任何写之前**判掉（A9 的零写入不止针对开关关闭）。
        """
        anchor = _method_block(
            _controller_source(),
            "public IActionResult TrendingAnchor(",
            "private static List<(string Title")
        self.assertIn('error = "year is required"', anchor, "缺年份没有回 400 year is required")
        missing = anchor.index('error = "year is required"')
        for later in ("RefreshMediaPaths()", "System.IO.Directory.CreateDirectory", "System.IO.File.Move"):
            self.assertLess(missing, anchor.index(later),
                            f"年份校验排在了 {later} 之后：缺年份也可能写库")

    def test_a_positive_exists_requires_a_year(self) -> None:
        """library-status 没有年份时不得宣称「已在库」——那是无法确认的宣称。

        TMDb 缺年份的老片很常见：按标题匹配会把同名不同年的库内条目判成命中，而命中会
        禁用卡片的「关注」按钮（用户下不了片）。保守回 false 只是显示成未入库，危害小得多。
        """
        # 含 docstring：这条保守策略必须写在代码里（否则下一个人会当成漏判改掉）
        block = _method_block(
            _controller_source(),
            "「热门」页的批量查库",
            "public IActionResult TrendingAnchor(")
        self.assertIn("query.Year.HasValue", block, "exists 的判定里没有「必须有年份」这个前提")
        self.assertIn("保守", block, "这条保守策略必须写在代码里（否则下一个人会当成漏判改掉）")
        # 年份必须出现在 exists 表达式的最前面：写在已知子句后面就等于没起作用
        exists_line = [line for line in block.splitlines() if "exists = " in line]
        self.assertEqual(len(exists_line), 1, "exists 的判定应当只有一处：" + str(exists_line))
        self.assertIn("query.Year.HasValue", exists_line[0],
                      "exists = 后面没有先判年份：缺年份仍可能由标题命中回 true")

    def test_both_endpoints_accept_the_platform_kind_vocabulary(self) -> None:
        """类别词表是跨层契约：面板/后端发 movie|episode|variety|record，库里叫 Series。

        端点只认 movie|series 时，剧集「关注」会被判成非法 kind、占位永不落盘（review r1 的
        F1）。归一是端点的责任：两条端点共用 NormalizeKind，落点类别决定分类目录。
        """
        source = _controller_source()
        body = _method_block(source, "private static string? NormalizeKind(", "private static string[] LibraryKindsFor(")
        for alias in ("episode", "variety", "tv", "record"):
            self.assertIn(f'"{alias}"', body, f"类别归一漏了面板/后端会发的 {alias!r}")
        self.assertIn("=> \"series\"", body.replace("'", "\""), "episode/variety 没有归到 series")
        self.assertIn('=> "record"', body, "record 没有归到 record 落点类别")

        anchor = _method_block(source, "public IActionResult TrendingAnchor(",
                               "private static List<(string Title")
        self.assertIn("NormalizeKind(kind)", anchor, "anchor 没有走类别归一")
        self.assertIn("PlaceholderRelativePath(safeTitle, yearText, normalizedKind)", anchor,
                      "anchor 没有把归一类别交给落点选择")
        placeholder = _method_block(source, "private static string PlaceholderRelativePath(",
                                   "private static bool IsWithinRoot(")
        for name in TEMPLATE_NAMES:
            self.assertIn(name, placeholder, f"落点选择没有覆盖 {name}")

        # 查库的载荷在 ReadTrendingQueries 里归一（两条端点共用同一个 NormalizeKind）
        status = _method_block(source, "private static List<(string Title",
                               "private static List<(string Kind")
        self.assertIn("NormalizeKind(kind)", status, "library-status 的载荷没有走同一套类别归一")
        self.assertIn("LibraryKindsFor(matchKind)", status, "查库没有按归一类别选库内类别")

    def test_the_proxy_whitelist_carries_the_trending_prefix(self) -> None:
        block = _method_block(_controller_source(), "private static readonly string[] AllowedPrefixes", "};")
        self.assertIn('"trending"', block,
                      "白名单少了 trending：/api/trending 与 /api/trending/watch 会被代理回 404")

    def test_the_client_fragment_is_registered_after_05_render(self) -> None:
        block = _method_block(_controller_source(), "ScriptSegments =", "};")
        segments = re.findall(r'"([^"]+)"', block)
        self.assertIn("06_trending", segments, "ScriptSegments 没有登记 06_trending")
        self.assertLess(segments.index("05_render"), segments.index("06_trending"),
                        "06_trending 必须排在 05_render 之后（分片顺序就是浏览器里的代码顺序）")

    def test_the_tmdb_key_is_injected_only_when_configured(self) -> None:
        manager = BACKEND_MANAGER.read_text(encoding="utf-8")
        self.assertEqual(manager.count("JMD_TMDB_API_KEY"), 1,
                         "key 只允许有一个注入点，多出来的一处很可能绕过了非空判断")
        guard = manager.index("if (tmdbApiKey.Length > 0)")
        inject = manager.index('info.Environment["JMD_TMDB_API_KEY"] = tmdbApiKey;')
        self.assertLess(guard, inject, "注入必须包在「非空才注入」的判断里")
        self.assertNotIn("JMD_TMDB_API_KEY", _controller_source(),
                         "key 不该出现在控制器里（它只经环境变量进后端）")

    def test_the_guidance_text_has_one_home_in_the_plugin(self) -> None:
        """无 key 的引导文案只有插件侧一个家：GET config 的 guidance 字段。

        面板优先读 config.guidance；后端 /api/trending 的 guidance 保留给直连 API 的消费者。
        文案常量必须存在、并且只在「没配 key」时下发（有 key 时下发 null，面板不能把空当没配）。
        """
        source = _controller_source()
        self.assertRegex(source, r'private const string TmdbGuidance = "[^"]+"',
                         "缺少 TmdbGuidance 常量（文案的唯一出处）")
        block = _method_block(source, "public IActionResult GetConfig()", "public IActionResult UpdateConfig(")
        self.assertIn("guidance =", block, "GET config 没有下发 guidance")
        self.assertIn("? null : TmdbGuidance", block, "guidance 没有按「有 key 时不下发」处理")

    def test_the_config_page_group_round_trips_its_fields(self) -> None:
        """「热门」分组的 DOM ↔ 脚本契约：load 回显 + save 提交，四个字段都不丢。"""
        page = CONFIG_PAGE.read_text(encoding="utf-8")
        plugin_config = PLUGIN_CONFIG.read_text(encoding="utf-8")

        # 四个复选框的 data-list 与 C# 的默认榜单逐一同序（页面不另抄一份清单）
        self.assertEqual(
            re.findall(r'data-list="([^"]+)"', page),
            _string_array(plugin_config, "DefaultTrendingLists"),
            "配置页的榜单复选框与 C# 内置默认不一致（顺序即展示顺序）")

        render_block = _method_block(page, "function renderTrending(", "function readTrendingLists(")
        for key in ("tmdbLanguage", "trendingLists", "anchorEnabled", "tmdbApiKeyConfigured"):
            self.assertIn(key, render_block, f"load 回显漏了 {key}")
        load_block = _method_block(page, "function load()", "function save()")
        self.assertIn("renderTrending(config)", load_block, "load() 没有调用热门的回显")

        save_block = _method_block(page, "function save()", "function errorText(")
        for key in ("TmdbApiKey", "TmdbLanguage", "TrendingLists", "AnchorEnabled"):
            self.assertIn(f"{key}:", save_block, f"save() 没有提交 {key}")
        self.assertIn("readTrendingLists()", save_block)

        # key：密码框 + 永不回填 + 有「已配置 / 未配置」提示行
        self.assertRegex(page, r'id="jdl-tmdb-key"[^>]*type="password"')
        self.assertIn("key.value = ''", render_block, "key 输入框必须清空回显（后台不下发 key）")
        self.assertIn('id="jdl-tmdb-key-state"', page, "缺少「已配置 / 未配置」提示行")
        # 说明里必须写明「留空并保存 = 清除」，否则用户会以为空着 = 不动它
        self.assertIn("留空并保存 = 清除", page)
        self.assertIn("不会下发到浏览器", page)


def _string_array(source: str, name: str) -> list[str]:
    """取 C# 里 `string[] NAME = { "a", "b" };` 的字面量（恰好一处）。"""
    pattern = re.compile(r"string\[\]\s+%s\s*=\s*\{(.*?)\};" % re.escape(name), re.S)
    found = pattern.findall(source)
    if len(found) != 1:
        raise AssertionError(f"{name} 期望恰好 1 处字符串数组，实际 {len(found)} 处")
    return re.findall(r'"([^"]*)"', found[0])


if __name__ == "__main__":
    unittest.main()
