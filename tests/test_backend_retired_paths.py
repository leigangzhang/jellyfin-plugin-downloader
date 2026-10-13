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
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import source_config  # noqa: E402

# f-string 的字面前缀至少这么长才算「疑似拼出来的开关」：太短（"--"、"_"）会误伤。
FRAGMENT_MIN_LEN = 8

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


NEEDLES = RETIRED_SYMBOLS | RETIRED_CONSTANTS


def _literal_offences(module_name, lineno, value):
    """这个字符串字面量本身就是退役通道的名字吗。

    `getattr(obj, "measured_speed_mbps")` / `data["--pansou"]` 里它不会作为 Name 或
    Attribute 出现，只看节点类型抓不到，所以要单独比一次字符串。
    """
    if value in NEEDLES:
        return [f"{module_name}:{lineno} 字面量 {value!r}"]
    return []


def _prefix_offences(module_name, lineno, literal):
    """f-string 的字面前缀是不是某个退役开关的开头（`f"--speed-{name}"`）。

    只做前缀判定，不做包含判定：`"allow_pan"`（合法的云盘开关）包含在
    `allow_pansou` 里但不是它的前缀，不该红。
    """
    if len(literal) < FRAGMENT_MIN_LEN:
        return []
    for token in NEEDLES:
        if token.startswith(literal):
            return [f"{module_name}:{lineno} f-string 拼出来的开关前缀 {literal!r}"
                    f"（疑似 {token!r}）"]
    return []


def _fold_strings(node):
    """把 `"a" + "b"` 折成 `"ab"`；折不动（含变量）返回 None。"""
    if (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_strings(node.left)
        right = _fold_strings(node.right)
        if left is not None and right is not None:
            return left + right
    return None


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
                    offenders.extend(_literal_offences(path.name, node.lineno, node.value))
                elif isinstance(node, ast.JoinedStr):
                    # f-string：只看字面部分（`f"--speed-{name}"` 的字面部分是 `--speed-`）。
                    # `{"a" + "b"}` 这类占位表达式由下面的 BinOp 规则单独处理。
                    literal = "".join(
                        part.value for part in node.values
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    )
                    offenders.extend(_prefix_offences(path.name, node.lineno, literal))
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                    folded = _fold_strings(node)
                    if folded is not None:
                        offenders.extend(_literal_offences(path.name, node.lineno, folded))
        self.assertEqual(offenders, [], "已退役的通道又出现在 backend 里：" + "; ".join(offenders))

    def test_console_has_no_manual_verify_endpoint(self):
        text = (BACKEND / "console_server.py").read_text(encoding="utf-8")
        self.assertNotIn("/api/verify", text)
        self.assertNotIn("_api_verify", text)

class CloudChannelWiringTests(unittest.TestCase):
    """驱动真实的 `console_server._run_search_job`，只把 CLI 调用换成记录器。"""

    def setUp(self):
        import build_console as bc  # noqa: PLC0415 - 导入控制台栈较重，放在用例里
        import console_server as cs  # noqa: PLC0415

        self.bc, self.cs = bc, cs
        self.tmp = tempfile.mkdtemp(prefix="jmd-cloud-wiring-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_round(self, sites, write_candidates=False):
        """跑一轮搜索任务，返回 (所有 CLI argv, job)。

        `write_candidates=True` 时记录器会在 search_pool 那一步真的写出候选文件，
        用来区分「本轮写的」与「上一轮留在同一个路径上的」。
        """
        calls = []
        snapshots = os.path.join(self.tmp, "snapshots")
        marks = os.path.join(self.tmp, "marks")
        os.makedirs(snapshots, exist_ok=True)
        os.makedirs(marks, exist_ok=True)
        params = {
            "title": "漫长的季节", "kind": "episode", "category": "Shows",
            "slug": "demo", "key": "demo", "duration": 50.0, "year": "",
            "alt_title": [], "season": 1, "episode": None, "sources": "both",
            "original_title": "", "strict_season": True, "publisher_only": True,
            "final_dir": self.tmp, "probe_kind": "episode",
            "pool_file": os.path.join(self.tmp, "pool.json"),
            "cand_file": os.path.join(self.tmp, "cands.magnets"),
            "probe_out": os.path.join(snapshots, "demo.probe.json"),
        }

        def record_cli(argv, timeout=600):
            calls.append(list(argv))
            return 0, "", ""

        def record_stream(argv, on_line=None, timeout=600):
            calls.append(list(argv))
            if write_candidates and argv and argv[0] == "search_pool.py":
                Path(os.path.join(self.tmp, "cands.magnets")).write_text(
                    "magnet:?xt=urn:btih:" + "A" * 40 + "\n", encoding="utf-8")
            return 0, []

        patches = [
            mock.patch.object(self.bc, "SNAPSHOTS_DIR", snapshots),
            mock.patch.object(self.bc, "MARKS_DIR", marks),
            mock.patch.object(self.bc, "list_thunder_tasks", lambda: []),
            mock.patch.object(self.bc, "list_watches", lambda: []),
            mock.patch.object(self.cs, "run_cli", record_cli),
            mock.patch.object(self.cs, "run_cli_stream", record_stream),
            mock.patch.dict(os.environ,
                            {source_config.ENV_SITES: json.dumps(sites,
                                                                 ensure_ascii=False)},
                            clear=False),
        ]
        for patcher in patches:
            patcher.start()
        try:
            jid = self.cs._new_job()
            self.cs._run_search_job(jid, params)
            job = dict(self.cs.JOBS[jid])
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        return calls, job

    def pan_calls(self, calls):
        return [argv for argv in calls if argv and argv[0] == "pan_search.py"]

    def probe_argv(self, calls):
        return next((argv for argv in calls
                     if argv and argv[0] == "probe_magnets.py"), [])

    def test_every_station_with_a_web_page_contributes_one_page_argument(self):
        sites = [
            dict(source_config.DEFAULT_SITES[0],
                 web_search_url="https://d.example/s?q={q}"),
            # 云盘专用站：只有网页搜索地址，没有接口地址
            {"name": "搜索页", "kind": "", "base_url": "",
             "web_search_url": "https://s.example/search?q={q}"},
        ]
        calls, job = self.run_round(sites)
        self.assertEqual(job.get("error"), "", job.get("error"))
        pan = self.pan_calls(calls)
        self.assertEqual(len(pan), 1, f"应当恰好调用一次 pan_search：{pan}")
        pages = [pan[0][i + 1] for i, arg in enumerate(pan[0]) if arg == "--page"]
        self.assertEqual(len(pages), 2, f"两个站各该带一个 --page：{pan[0]}")
        self.assertIn("d.example", pages[0])
        self.assertIn("s.example", pages[1])
        for url in pages:
            # 片名必须编码进 {q}，不能原样塞中文
            self.assertNotIn("{q}", url)
            self.assertNotIn("漫长的季节", url)

    def test_a_stale_candidate_file_is_not_fed_to_the_probe(self):
        """候选文件路径固定、内容分轮次：本轮没写就不能喂给 probe。

        只配云盘专用站（或配置坏掉）时 search_pool 会早退、不写候选文件，而
        /tmp/<slug>.magnets 还留着上一轮的内容——照旧传 `--candidate-file` 会把上一轮的
        磁力当成本轮结果（复核报告 F1/F2）。
        """
        stale = Path(self.tmp) / "cands.magnets"
        stale.write_text("magnet:?xt=urn:btih:" + "B" * 40 + "\n", encoding="utf-8")
        past = time.time() - 120
        os.utime(stale, (past, past))

        calls, job = self.run_round([dict(source_config.DEFAULT_SITES[0])])
        self.assertEqual(job.get("error"), "", job.get("error"))
        argv = self.probe_argv(calls)
        self.assertTrue(argv, "probe 应该被调用")
        self.assertNotIn("--candidate-file", argv,
                         "上一轮的候选文件不该被当成本轮结果喂给 probe")
        lines = job.get("lines") or []
        self.assertTrue(any("不拿上一轮的文件喂 probe" in line for line in lines), lines)

    def test_a_candidate_file_written_this_round_is_fed_to_the_probe(self):
        calls, job = self.run_round([dict(source_config.DEFAULT_SITES[0])],
                                    write_candidates=True)
        self.assertEqual(job.get("error"), "", job.get("error"))
        argv = self.probe_argv(calls)
        self.assertIn("--candidate-file", argv)

    def test_no_configured_web_page_means_no_pan_search_call(self):
        calls, job = self.run_round([dict(source_config.DEFAULT_SITES[0])])
        self.assertEqual(job.get("error"), "", job.get("error"))
        self.assertEqual(self.pan_calls(calls), [],
                         "没有任何站填网页搜索地址时不该调用 pan_search")
        lines = job.get("lines") or []
        self.assertTrue(any("云盘通道没有入口" in line for line in lines),
                        f"应当明说没有入口，而不是静默返回空：{lines}")


class RetiredFragmentGuardTests(unittest.TestCase):
    """守卫自身的负向对照：拼出来的名字要能红，合法同名不同词不能红。

    复核报告 F10：原来的扫描只比 `ast.Constant`，`getattr(o, "measured_speed_" + "mbps")`、
    `data["measured_speed_" + "mbps"]`、`f"--speed-{name}"` 都能绕过去。
    """

    def test_concatenated_and_f_string_names_are_flagged(self):
        for literal in ("measured_speed_mbps", "speed_probe_error", "--pansou"):
            with self.subTest(literal=literal):
                self.assertTrue(_literal_offences("m.py", 1, literal))
        for prefix in ("--speed-", "measured_speed_"):
            with self.subTest(prefix=prefix):
                self.assertTrue(_prefix_offences("m.py", 1, prefix))
        for expression in ('"measured_speed_" + "mbps"', '"--speed-" + name'):
            with self.subTest(expression=expression):
                folded = _fold_strings(ast.parse(expression, mode="eval").body)
                self.assertTrue(
                    folded is None or _literal_offences("m.py", 1, folded)
                    or _prefix_offences("m.py", 1, folded),
                    f"{expression} 既折不出退役名字也不算疑似前缀",
                )

    def test_legitimate_names_sharing_a_stem_are_not_flagged(self):
        # allow_pan（还在用的云盘开关）包含在 allow_pansou 里但不是它的前缀；
        # speed_mbps（迅雷取回任务的速度键）与退役的 measured_speed_mbps 同词根。
        for literal in ("allow_pan", "speed_mbps", "speed"):
            with self.subTest(literal=literal):
                self.assertEqual(_literal_offences("m.py", 1, literal), [])
        # `--extra-source-limit` 是**还在用**的开关（退役的只有 `--extra-source`
        # 与 `--extra-source-base-url`）；前者比退役的更长，不是它们的前缀，不该红。
        self.assertEqual(_prefix_offences("m.py", 1, "--extra-source-limit"), [])
        self.assertEqual(_literal_offences("m.py", 1, "--extra-source-limit"), [])


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
