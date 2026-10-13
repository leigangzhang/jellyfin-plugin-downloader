"""已退役通道的守卫 + 新的配置面（资源站 / L1 关键字）。

Subject: `backend/` 的模块表面与 `backend/source_config.py` 的配置契约。

2026-10-13 的简化删掉了三条路：本地 Pansou 实例（磁力与云盘两半）、浏览器搜磁力的
入口（`--magnet` / `--info-hash`）、aria2c 短窗测速。删除需要一个会变红的检查——否则
一个 flag 被重新加回来、一个模块被复活、一个读侧字段又被读起来，没有东西会失败。

判定走 AST：**符号、关键字参数名、当作常量出现的 CLI flag 与字典键**。注释和 docstring
允许点名这些通道（那是写给下一个读者的「别再把它加回来」），所以不按整段文本匹配。

夹具与网络都无关：这里只读仓库里的源文件。
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import source_config  # noqa: E402

# 退役的符号：任何名字（Name / Attribute / 关键字参数）都不许再出现。
RETIRED_SYMBOLS = frozenset({
    "speed_probe",
    "measure_torrent_speed",
    "speed_probe_error",
    "measured_speed_mbps",
    "speed_unmeasured",
    "allow_pansou",
    "allow_pansou_fallback",
    "pansou_search",
    "refresh_speed",
    "speed_top",
    "speed_seconds",
    "speed_warmup",
})

# 退役的常量：以**字符串字面量整体**出现的 CLI flag 与字典键（精确比较，所以
# `--extra-source` 退役不影响仍然存在的 `--extra-source-limit`）。
# `--base-url` 不在这里：`torrents_csv_source.py` 自己的 CLI 就有这个 flag，那是它
# 的接口，不是 search_pool 退役掉的那个。
RETIRED_CONSTANTS = frozenset({
    "--speed-probe",
    "--speed-seconds",
    "--speed-warmup",
    "--speed-top",
    "--refresh-speed",
    "--pansou",
    "--allow-pansou",
    "--allow-pansou-fallback",
    "--magnet",
    "--info-hash",
    "--extra-source",
    "--extra-source-base-url",
    "measured_speed_mbps",
    "speed_unmeasured",
    "speed_probe_error",
    "PANSOU_API",
    "pansou#",
})


def backend_modules():
    return sorted(path for path in BACKEND.glob("*.py"))


class RetiredPathsTests(unittest.TestCase):
    """删掉的通道必须真的消失，且不许以「读侧还在读」的形式留下。"""

    def test_speed_probe_module_is_gone(self):
        self.assertFalse(
            (BACKEND / "speed_probe.py").exists(),
            "aria2c 测速模块又回来了；它测不动排序，却要每条付 17.6–24.1s",
        )

    def test_no_retired_symbol_or_constant_in_the_backend(self):
        offenders = []
        for path in backend_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = None
                if isinstance(node, ast.Name):
                    name = node.id
                elif isinstance(node, ast.Attribute):
                    name = node.attr
                elif isinstance(node, ast.keyword):
                    name = node.arg
                if name in RETIRED_SYMBOLS:
                    offenders.append(f"{path.name}:{node.lineno} 符号 {name}")
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in RETIRED_CONSTANTS:
                        offenders.append(f"{path.name}:{node.lineno} 常量 {node.value!r}")
        self.assertEqual(offenders, [], "已退役的通道又出现在 backend 里：" + "; ".join(offenders))

    def test_console_has_no_manual_verify_endpoint(self):
        text = (BACKEND / "console_server.py").read_text(encoding="utf-8")
        self.assertNotIn("/api/verify", text)
        self.assertNotIn("_api_verify", text)

    def test_console_reads_the_station_list_for_the_cloud_channel(self):
        """云盘通道的入口来自配置，不是硬编码的本地实例。"""
        text = (BACKEND / "console_server.py").read_text(encoding="utf-8")
        self.assertIn("source_config.web_search_urls(", text)
        self.assertIn('"--page", url', text)
        self.assertIn("云盘通道没有入口", text)


class SourceConfigTests(unittest.TestCase):
    """`source_config` 是站点清单与关键字的唯一事实源。"""

    def test_empty_configuration_means_the_builtin_defaults(self):
        sites, errors = source_config.load_sites("")
        self.assertEqual(errors, [])
        self.assertEqual([site["name"] for site in sites],
                         [site["name"] for site in source_config.DEFAULT_SITES])
        self.assertEqual(source_config.load_keywords(""), (list(source_config.DEFAULT_ANCHOR_KEYWORDS), []))
        self.assertTrue(source_config.DEFAULT_ANCHOR_KEYWORDS)

    def test_defaults_are_not_shared_objects(self):
        """调用方改返回值不许改到默认值本身。"""
        sites, _ = source_config.load_sites("")
        sites[0]["name"] = "mutated"
        self.assertNotEqual(source_config.DEFAULT_SITES[0]["name"], "mutated")

    def test_configured_stations_replace_the_defaults(self):
        text = json.dumps([
            {"name": "A", "kind": "diao", "base_url": "https://a.example/api",
             "web_search_url": "https://a.example/s?q={q}"},
        ])
        sites, errors = source_config.load_sites(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0]["name"], "A")
        self.assertEqual(sites[0]["web_search_url"], "https://a.example/s?q={q}")

    def test_bad_station_entries_are_reported_and_never_silently_defaulted(self):
        cases = {
            "不是 JSON": "not json",
            "不是数组": json.dumps({"kind": "diao"}),
            "未知类型": json.dumps([{"kind": "nope", "base_url": "https://x/y"}]),
            "非 http 地址": json.dumps([{"kind": "diao", "base_url": "ftp://x"}]),
            "两个入口都空": json.dumps([{"kind": "diao"}]),
            "网页搜索地址缺 {q}": json.dumps([
                {"kind": "diao", "base_url": "https://x/y",
                 "web_search_url": "https://x/s"}]),
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                sites, errors = source_config.load_sites(text)
                self.assertEqual(sites, [])
                self.assertTrue(errors, f"{label} 应当留下错误明细")

    def test_a_cloud_only_station_is_allowed(self):
        """只有网页搜索地址、没有接口地址的站是合法的云盘专用站。

        只发云盘资源、没有搜索 API 的站（高清剧集网那一类）正是云盘通道的常见入口；
        要求它必须填接口地址等于让 ② 那条通道配不出来。
        """
        text = json.dumps([{"name": "搜索页", "kind": "diao", "base_url": "",
                            "web_search_url": "https://s.example/search?q={q}"}])
        sites, errors = source_config.load_sites(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(sites), 1)
        # 没有 API 要解析，类型归一化成空串——不能被取源侧当成磁力站。
        self.assertEqual(sites[0]["kind"], "")
        self.assertEqual(source_config.magnet_sites(sites), ([], []))
        # 云盘入口照常有
        self.assertEqual(len(source_config.web_search_urls("漫长的季节", sites)), 1)

    def test_magnet_sites_keeps_only_the_api_stations(self):
        api, extra = source_config.magnet_sites([
            {"name": "a", "kind": "diao", "base_url": "https://a/api", "web_search_url": ""},
            {"name": "b", "kind": "torrents-csv", "base_url": "https://b/api", "web_search_url": ""},
            {"name": "c", "kind": "", "base_url": "", "web_search_url": "https://c/s?q={q}"},
        ])
        self.assertEqual([site["name"] for site in api], ["a"])
        self.assertEqual([site["name"] for site in extra], ["b"])

    def test_one_bad_station_does_not_discard_the_good_ones(self):
        text = json.dumps([
            {"kind": "nope", "base_url": "https://x/y"},
            {"name": "ok", "kind": "torrents-csv", "base_url": "https://ok.example/api"},
        ])
        sites, errors = source_config.load_sites(text)
        self.assertEqual([site["name"] for site in sites], ["ok"])
        self.assertEqual(len(errors), 1)

    def test_station_count_is_capped(self):
        text = json.dumps([
            {"kind": "diao", "base_url": f"https://s{i}.example/api"}
            for i in range(source_config.MAX_SITES + 3)
        ])
        sites, errors = source_config.load_sites(text)
        self.assertEqual(len(sites), source_config.MAX_SITES)
        self.assertTrue(any("最多" in error for error in errors))

    def test_keywords_accept_json_and_plain_lines(self):
        self.assertEqual(source_config.load_keywords('["甲", "乙"]')[0], ["甲", "乙"])
        self.assertEqual(source_config.load_keywords("甲\n乙,丙")[0], ["甲", "乙", "丙"])
        # 空行与前后空白不算条目；全是空白 = 错误，而不是「静默没有关键字」
        self.assertEqual(source_config.load_keywords('["  甲  ", "", "乙"]')[0], ["甲", "乙"])
        self.assertTrue(source_config.load_keywords('["", "   "]')[1])

    def test_an_explicit_empty_keyword_list_means_disable_this_tier(self):
        """显式空数组 = 不要锚点档（站点不能为空，关键字可以）。"""
        self.assertEqual(source_config.load_keywords("[]"), ([], []))

    def test_keyword_count_is_capped(self):
        text = json.dumps([f"k{i}" for i in range(source_config.MAX_KEYWORDS + 2)])
        keywords, errors = source_config.load_keywords(text)
        self.assertEqual(len(keywords), source_config.MAX_KEYWORDS)
        self.assertTrue(any("最多" in error for error in errors))

    def test_web_search_urls_fill_title_and_skip_stations_without_one(self):
        sites = [
            {"name": "with-page", "kind": "diao", "base_url": "https://a/api",
             "web_search_url": "https://a/s?q={q}"},
            {"name": "no-page", "kind": "torrents-csv", "base_url": "https://b/api",
             "web_search_url": ""},
        ]
        found = source_config.web_search_urls("漫长的季节", sites)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "with-page")
        self.assertTrue(found[0][1].startswith("https://a/s?q="))
        self.assertNotIn("漫长的季节", found[0][1], "片名必须被 URL 编码")

    def test_web_search_urls_is_empty_when_no_station_declares_a_page(self):
        """没有入口要能被调用方看出来，而不是返回一个「搜了但没结果」的空。"""
        self.assertEqual(source_config.web_search_urls("x", source_config.DEFAULT_SITES), [])

    def test_torrents_csv_kind_matches_the_source_module_name(self):
        """kind 字符串就是适配器名，两边必须逐字相同。"""
        import torrents_csv_source

        self.assertEqual(source_config.TORRENTS_CSV_KIND, torrents_csv_source.SOURCE_NAME)


if __name__ == "__main__":
    unittest.main()
