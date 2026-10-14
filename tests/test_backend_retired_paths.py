"""已退役通道的守卫 + 新的配置面（资源站 / L1 关键字）。

Subject: `backend/` 的模块表面与 `backend/source_config.py` 的配置契约。

2026-10-13 的简化删掉了三条路：本地 Pansou 实例（磁力与云盘两半）、浏览器搜磁力的
入口（`--magnet` / `--info-hash`）、aria2c 短窗测速。删除需要一个会变红的检查——否则
一个 flag 被重新加回来、一个模块被复活、一个读侧字段又被读起来，没有东西会失败。

判定走 AST：**符号、关键字参数名、import 的模块名、当作常量出现的 CLI flag 与字典键**。
注释和 docstring 允许点名这些通道（那是写给下一个读者的「别再把它加回来」），所以不按整段
文本匹配。

**它是烟雾报警器，不是证明器**：能静态折出来的写法（字面量、`"a" + "b"`、f-string 前缀、
`import` 的模块名）都会红；而 `"".join([...])`、`%` 格式化、`bytes` 解码、先拼进变量再传、
以及换一个全新名字复活同一条路——这些它抓不到（复核第二轮 F5 实测）。所以它挡的是
「原样/近样复活」，不是「故意绕过」。

夹具与网络都无关：这里只读仓库里的源文件。
"""

from __future__ import annotations

import ast
import json
import re
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


def _skip_string(source: str, index: int):
    """从引号处跳过整个字面量（含 `""` 转义与逐字字符串），返回结束后的下标。"""
    quote = source[index]
    verbatim = index > 0 and source[index - 1] == "@"
    index += 1
    length = len(source)
    while index < length:
        if verbatim and source[index] == '"' and source[index : index + 2] == '""':
            index += 2
            continue
        if not verbatim and source[index] == "\\":
            index += 2
            continue
        if source[index] == quote:
            return index + 1
        index += 1
    return length


def anonymous_object_keys(source: str) -> list:
    """把一个 C# 源文件里所有 `new { ... }` 匿名对象的本层键名收集出来。

    用途是找「同一个匿名对象里两个键只差大小写」——C# 编译通过，但
    System.Text.Json 序列化时会抛 "property name collides with another property"，
    表现是整个接口 500，而构建与单测都不会红。

    字符级扫描，并且要跳过会把花括号计数带偏的东西：字符串与字符字面量、注释、
    特性（`[Authorize(Policy = "...")]` 里的 `Policy =` 不是匿名对象的键）、
    以及 `=>` / `==` 这类不是赋值的等号。
    """
    keys = []
    depth = 0
    anon_depth = None
    pending_new = False
    in_attribute = False
    index = 0
    length = len(source)
    while index < length:
        char = source[index]

        if char in " \t\r\n":
            index += 1
            continue
        if char == '"' or char == "'":
            index = _skip_string(source, index)
            pending_new = False
            continue
        if source[index : index + 2] == "//":
            index = source.find("\n", index)
            index = length if index < 0 else index + 1
            continue
        if source[index : index + 2] == "/*":
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char == "[" and depth >= 1:
            # 特性只出现在类型/成员声明上，不在匿名对象体内；跳过整个 `[...]`
            in_attribute = True
            index += 1
            continue
        if char == "]" and in_attribute:
            in_attribute = False
            index += 1
            continue
        if in_attribute:
            index += 1
            continue

        # 标点先处理（`{` / `}` 可能紧跟在标识符后面，比如 `new {` 里的花括号）
        if char == "{":
            depth += 1
            if pending_new:
                # 只有「紧跟在 new 后面」的花括号才是匿名对象
                anon_depth = depth
            pending_new = False
            index += 1
            continue
        if char == "}":
            depth -= 1
            if anon_depth is not None and depth < anon_depth:
                anon_depth = None
            pending_new = False
            index += 1
            continue

        word = re.match(r"[A-Za-z_][A-Za-z0-9_]*", source[index:])
        if word:
            token = word.group(0)
            end = index + len(token)
            look = end
            while look < length and source[look] in " \t\r\n":
                look += 1
            was_pending_new = pending_new
            # `new` 只可能紧邻 `{`：中间出现别的标识符说明它是 `new List<...>()`，
            # 不清掉待定状态的话，后面随便一个 `{` 都会被当成匿名对象开头。
            pending_new = token == "new"
            if anon_depth is not None and depth == anon_depth \
                    and source[look : look + 1] == "=" and source[look : look + 2] not in ("==", "=>"):
                keys.append(token)
            index = end
            continue

        if char == ";":
            pending_new = False
        elif char == ",":
            pending_new = False
        index += 1

    return keys


def backend_modules():
    return sorted(path for path in BACKEND.glob("*.py"))


def pick_free_port() -> int:
    """借一个当前没人监听的端口（绑 0 让内核给，立刻释放）。"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


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
                elif isinstance(node, ast.Import):
                    # `import speed_probe` / `import speed_probe as sp`：别名换不掉模块名
                    for module in node.names:
                        root = module.name.split(".")[0]
                        if root in RETIRED_SYMBOLS:
                            offenders.append(
                                f"{path.name}:{node.lineno} import 了退役模块 {root!r}")
                    continue
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    if root in RETIRED_SYMBOLS:
                        offenders.append(
                            f"{path.name}:{node.lineno} 从退役模块 {root!r} 导入")
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

    def test_each_round_gets_its_own_candidate_file(self):
        """候选文件按轮命名：同名标题连开两轮不会互相读到对方的候选。

        复核第二轮 F3：固定 `/tmp/<slug>.magnets` 时只能靠 mtime 判断「是不是本轮写的」，
        而两个写者并发（同一季重搜、或不同季但同一个 slug）时那条判定就失效了。
        """
        captured = []
        bodies = [
            {"title": "漫长的季节", "kind": "episode", "sources": "magnet",
             "final_dir": self.tmp},
            {"title": "漫长的季节", "kind": "episode", "sources": "magnet",
             "final_dir": self.tmp},
        ]
        fake_self = mock.Mock()
        with mock.patch.object(self.cs, "_run_search_job",
                               lambda jid, params: captured.append(params)), \
                mock.patch.object(self.cs, "valid_target_dir", lambda _path: True), \
                mock.patch.object(self.cs.Handler, "_ok", lambda _self, obj: obj):
            for body in bodies:
                self.cs.Handler._api_search(fake_self, dict(body))

        self.assertEqual(len(captured), 2)
        paths = [params["cand_file"] for params in captured]
        self.assertNotEqual(paths[0], paths[1],
                            "两轮用了同一个候选文件路径，并发时会互相串候选")
        for path in paths:
            self.assertIn("漫长的季节", path)
            self.assertTrue(path.endswith(".magnets"), path)

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


class SiteUrlClassificationTests(unittest.TestCase):
    """配置页一个站只有一个地址输入框，所以「地址 → 哪个入口」必须由地址本身判定。

    `classify_site_url` 是这条判定的唯一事实源：配置页显示识别结果、C# 保存时补 kind、
    `normalise_site` 归一化都按它走。判定错一格，用户粘进去的站就会走错适配器。
    """

    def test_a_bare_host_gets_the_builtin_search_path(self):
        """只填主机名时，要落到与内置默认站同一个入口。"""
        self.assertEqual(source_config.classify_site_url("https://m.diao.im"),
                         ("diao", "https://m.diao.im/api/search", ""))
        self.assertEqual(source_config.classify_site_url("https://torrents-csv.com"),
                         ("torrents-csv", "https://torrents-csv.com/service/search", ""))

    def test_an_explicit_path_is_kept_verbatim(self):
        self.assertEqual(source_config.classify_site_url("https://m.diao.im/api/search"),
                         ("diao", "https://m.diao.im/api/search", ""))
        self.assertEqual(source_config.classify_site_url("https://torrents-csv.com/service/search"),
                         ("torrents-csv", "https://torrents-csv.com/service/search", ""))

    def test_a_query_template_is_a_cloud_page(self):
        kind, base_url, web = source_config.classify_site_url("https://s.example/search?q={q}")
        self.assertEqual((kind, base_url), ("", ""))
        self.assertEqual(web, "https://s.example/search?q={q}")

    def test_an_unknown_host_is_not_claimed_as_a_magnet_station(self):
        self.assertEqual(source_config.classify_site_url("https://unknown.example/x"), ("", "", ""))

    def test_normalise_site_takes_the_single_url_the_page_sends(self):
        """配置页只发 BaseUrl（kind 空、web_search_url 空），这里要能补全。"""
        site, error = source_config.normalise_site({"name": "", "kind": "", "base_url": "https://m.diao.im"})
        self.assertEqual(error, "")
        self.assertEqual(site["kind"], "diao")
        self.assertEqual(site["base_url"], "https://m.diao.im/api/search")
        self.assertEqual(site["name"], "diao.im")

        cloud, error = source_config.normalise_site({"name": "", "kind": "", "base_url": "https://pan.example/s?q={q}"})
        self.assertEqual(error, "")
        self.assertEqual(cloud["kind"], "")
        self.assertEqual(cloud["base_url"], "")
        self.assertEqual(cloud["web_search_url"], "https://pan.example/s?q={q}")

    def test_legacy_dual_entry_sites_keep_the_magnet_entry(self):
        """老 XML 里同时有接口地址与网页地址的站，不能因为这次归一化丢掉磁力入口。"""
        site, error = source_config.normalise_site({
            "name": "A", "kind": "diao", "base_url": "https://a.example/api",
            "web_search_url": "https://a.example/s?q={q}",
        })
        self.assertEqual(error, "")
        self.assertEqual(site["base_url"], "https://a.example/api")
        self.assertEqual(site["kind"], "diao")
        self.assertEqual(site["web_search_url"], "https://a.example/s?q={q}")

    def test_an_unknown_kind_is_still_rejected(self):
        """「配了但没人认账的类型」不能静默通过。"""
        site, error = source_config.normalise_site({"kind": "nope", "base_url": "https://x/y"})
        self.assertIsNone(site)
        self.assertIn("未知的资源站类型", error)

    def test_an_unknown_host_without_a_declared_kind_is_rejected(self):
        """认不出来的地址 + 没声明类型 = 配置错误。

        静默兜成 diao 会让 `https://google.com` 变成「一个打不通的 diao 站」，
        用户只看到「搜了但没结果」。这条守卫就钉住「不许静默兜底」。
        """
        site, error = source_config.normalise_site({"kind": "", "base_url": "https://google.com"})
        self.assertIsNone(site)
        self.assertIn("识别不出", error)
        # 没有 kind 键也一样（配置页发的就是这个形状）
        site, error = source_config.normalise_site({"base_url": "https://unknown.example/x"})
        self.assertIsNone(site)
        self.assertIn("识别不出", error)

    def test_an_explicit_kind_still_carries_a_self_hosted_station(self):
        """老 XML / 脚本显式声明了类型时，认不出的主机按声明走，不被这次收紧打掉。"""
        site, error = source_config.normalise_site(
            {"name": "自建", "kind": "diao", "base_url": "https://self.example/api"})
        self.assertEqual(error, "")
        self.assertEqual(site["kind"], "diao")
        self.assertEqual(site["base_url"], "https://self.example/api")

    def test_the_page_cannot_save_an_address_the_backend_cannot_classify(self):
        """配置页侧的同一条守卫：页面恒发 Kind=""，所以未知主机必须被 C# 校验拒掉。

        用源码断言而不是起 Jellyfin：改造后 C# 的「认不出来」必须走 KindForUrl 返回
        空串的分支，不能自己兜一个 diao 出来。
        """
        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        self.assertIn("private static string KindForUrl(string url)", controller)
        self.assertIn("KindForUrl(address).Length == 0", controller,
                      "ValidateBtSites 必须用 KindForUrl 的空串结果拦下未知地址")
        self.assertIn("识别不出这个地址该走哪个入口", controller)

    def test_the_page_round_trips_the_station_kind(self):
        """手改 XML 的自建站（未知主机 + 显式 kind）不能被配置页一次保存搞丢。

        页面只给一个地址输入框，类型没有可编辑的入口；所以后端回显的 kind 必须被
        原样存回来，并由 save 原样发出去——否则 ValidateBtSites 会把整单拒掉，
        连同一个页面里的其它修改也一起存不下去。
        """
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        self.assertIn("row._kind", page, "站点行必须记住后端给的 kind")
        self.assertIn("Kind: item.kind", page, "save 必须把 kind 原样发回")
        self.assertIn("siteHintFor(item.address, row._kind)", page, "自定义站要按声明类型显示")

    def test_the_http_proxy_follows_the_effective_port(self):
        """面板的数据面调用必须走「实际监听端口」，不能走配置里的固定 URL。

        `PluginConfiguration.BackendBaseUrl` 全仓没有写入点（永远停在 8123），而端口
        被占用时后端会自增到 8124——代理照那个字段转发的话，面板的 snapshot/search/
        submit/watch/verify/mark/cleanup/pan 全部 502，「端口占用自增」端到端失效。
        这里同时钉住两侧：C# 代理不得再直接读那个字段，且代理拼出的目标地址在
        自增端口上真的能打到后端。
        """
        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        proxy = controller[controller.index("public async Task Proxy(string path)"):]
        proxy = proxy[:proxy.index("private static bool IsAllowed")]
        # 只看可执行代码，注释里点名这个字段是允许的（那是写给下一个读者的）
        code = "\n".join(line for line in proxy.splitlines()
                         if not line.strip().startswith("//"))
        self.assertNotIn("BackendBaseUrl", code, "代理必须走 BackendManager.BaseUrl")
        self.assertIn("var baseUrl = BackendManager.BaseUrl;", code)

        # 行为侧：起一个真实后端，把「自增端口」当 EffectivePort，按代理的拼法打一发
        import subprocess
        import tempfile
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            port = pick_free_port()
            env = dict(os.environ, JMD_DATA_DIR=tmp)
            server = subprocess.Popen(
                [sys.executable, "-u", str(BACKEND / "console_server.py"),
                 "--port", str(port)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                # 事件驱动等启动：后端就绪时会打出含端口的启动行，读出它即可，
                # 不轮询、不 sleep（`tools/check-invariants.py` 有 no-time-based-test-sync）。
                ready = ""
                for line in server.stdout:
                    if f":{port}/" in line or "JMD 控制台" in line:
                        ready = line.strip()
                        break
                self.assertTrue(ready, "后端没有打出启动行")

                # 代理拼法（Controller.Proxy）：baseUrl + "/api/" + path + query
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/snapshot?slug=jdlping")
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(payload.get("service"), "jellyfin-downloader-backend")
                self.assertIn("candidates", payload, "打到的不像快照响应")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()

    def test_paths_with_spaces_reach_the_backend_unharmed(self):
        """媒体根含空格（如 /Volumes/XIAOMI SSD2/Media）时必须完整传到后端。

        `BackendManager.Start` 把 python / 脚本 / 日志拼进 `/bin/sh -c` 的字符串，
        所以任何拼进去的值都会被 shell 再解析一遍：不加引号的路径会被空格切成两半
        （实机踩到过——两个环境变量都变成 /Volumes/XIAOMI，下载会写到错的地方）。
        这里用带空格的路径过一遍真实的拼串与 shell 解析。
        """
        import shlex
        import subprocess

        media_root = "/Volumes/XIAOMI SSD2/Media"
        staging_root = "/Volumes/XIAOMI SSD2/Media/.staging"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "console_server.py"
            script.write_text("import os\nprint(repr(os.environ.get('JMD_MEDIA_ROOT')))\n",
                              encoding="utf-8")
            log = Path(tmp) / "backend.log"

            # 与 BackendManager.Start 同一拼法：三条路径必须各自加单引号。
            # 先钉住生产代码里真的是这个形状，下面跑的才算「生产拼法」而不是测试自己复读
            # 一遍引号（否则把 C# 里的引号删掉，这条用例照样绿，就失去守卫意义了）。
            manager = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "BackendManager.cs").read_text(encoding="utf-8")
            self.assertIn(
                """var command = $"exec '{python}' '{script}' >> '{log}' 2>&1";""",
                manager, "python / 脚本 / 日志三条路径必须各自加单引号")
            command = "exec '%s' '%s' >> '%s' 2>&1" % (sys.executable, script, log)
            env = dict(os.environ,
                       JMD_MEDIA_ROOT=media_root,
                       JMD_STAGING_ROOT=staging_root,
                       JMD_PORT="8124")
            proc = subprocess.run(["/bin/sh", "-c", command], env=env,
                                  capture_output=True, text=True, timeout=30)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            # 启动行把 stdout 重定向进日志（与 BackendManager 的拼法一致），所以读日志
            self.assertTrue(log.exists(), "日志重定向没生效")
            self.assertEqual(log.read_text(encoding="utf-8").strip(), repr(media_root),
                             "媒体根被 shell 切断了——下载会落到错误目录")
            # 整条命令行按 shell 规则切出来的 argv：三条路径各占一个参数，不多不少
            argv = shlex.split(command)
            self.assertEqual(argv[0], "exec")
            self.assertIn(script.as_posix(), argv)
            self.assertIn(log.as_posix(), argv)
            self.assertEqual(env["JMD_STAGING_ROOT"], staging_root)

        # 端口不再拼进命令行，而是走后端读得到的 JMD_PORT
        manager = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "BackendManager.cs").read_text(encoding="utf-8")
        self.assertIn('info.Environment["JMD_PORT"]', manager)
        self.assertNotIn("--port {port}", manager)
        backend = (BACKEND / "console_server.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("JMD_PORT")', backend)

    def test_the_page_shows_the_builtin_defaults_when_config_is_empty(self):
        """配置为空时页面要显示内置默认站点/关键词，而不是一排空输入框。

        需求是「系统内置两个资源站点，默认显示完整站点名」。后端在配置为空时会用
        内置默认，但那只发生在启动参数里——页面照 `btSites` 渲染，空了就是空白，
        用户看不到默认值。所以 C# 侧也存了一份默认，这里钉住它与 Python 侧逐字一致
        （两份默认一旦分叉，页面显示的和后端实际用的就不是一回事）。
        """
        import source_config

        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        sites_block = controller[controller.index("private static readonly string[] DefaultSiteUrls"):]
        keywords_block = sites_block[sites_block.index("private static readonly string[] DefaultKeywords"):]
        cs_urls = re.findall(r'"(https://[^"]+)"', sites_block[:sites_block.index("DefaultKeywords")])
        cs_keywords = re.findall(r'"([^"]+)"', keywords_block[:keywords_block.index("};")])

        self.assertEqual(cs_urls, [site["base_url"] for site in source_config.DEFAULT_SITES],
                         "C# 内置默认站点与 source_config.DEFAULT_SITES 不一致")
        self.assertEqual(cs_keywords, list(source_config.DEFAULT_ANCHOR_KEYWORDS),
                         "C# 内置默认关键词与 source_config.DEFAULT_ANCHOR_KEYWORDS 不一致")
        # 空配置时走默认这一分支必须存在
        self.assertIn("DefaultSiteUrls", controller)
        self.assertIn("config?.BtSites is { Count: > 0 }", controller)
        self.assertIn("config?.L1Keywords is { Count: > 0 }", controller)

    def test_no_json_payload_has_case_colliding_keys(self):
        """JSON 响应里不许出现「只差大小写」的两个键。

        实机踩到过：`GetConfig` 的匿名对象里同时留着 `btSites` 与 `BtSites`，
        System.Text.Json 的冲突检测直接抛异常 → `/JellyfinDownloader/config` 整个 500
        → 配置页读不到任何配置（站点/关键词列表整块不渲染、端口显示 —）。
        编译器不报、构建与单测都不红，所以单独立一条守卫盯着。
        """
        offenders = []
        for path in sorted((ROOT / "Jellyfin.Plugin.JellyfinDownloader").rglob("*.cs")):
            seen = {}
            for key in anonymous_object_keys(path.read_text(encoding="utf-8")):
                seen.setdefault(key.lower(), set()).add(key)
            for lower, variants in seen.items():
                if len(variants) > 1:
                    offenders.append(f"{path.name}: {sorted(variants)}")
        self.assertEqual(offenders, [],
                         "匿名对象里有只差大小写的键，序列化会 500：" + "; ".join(offenders))

    def test_the_config_endpoint_serves_one_site_field(self):
        """配置页读 `config.btSites`，所以这个 key 必须存在且唯一。"""
        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        config_action = controller[controller.index("public IActionResult GetConfig()"):]
        config_action = config_action[:config_action.index("public IActionResult UpdateConfig")]
        self.assertIn("btSites = sites", config_action)
        self.assertNotIn("BtSites = config", config_action)
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        self.assertIn("config.btSites", page, "配置页必须读这个唯一的字段名")

    def test_the_backend_announces_itself_with_the_same_marker_the_plugin_looks_for(self):
        """自身标识是 C# 与 Python 之间的字面量契约。

        插件侧靠它区分「我们的后端」和「恰好占着这个端口的外来 HTTP 服务」：
        两边字面量一旦不一致，插件就会认不出自己的后端（或反过来认错），
        而这两种失败在单侧测试里都看不见。
        """
        marker = "jellyfin-downloader-backend"
        backend = (BACKEND / "console_server.py").read_text(encoding="utf-8")
        manager = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "BackendManager.cs").read_text(encoding="utf-8")
        self.assertIn('SERVICE_ID = "%s"' % marker, backend)
        self.assertIn('ServiceMarker = "%s"' % marker, manager)
        # 后端要真的把它写进 JSON 响应；插件要真的解析响应体里的字段
        # （不能只读前若干字节：那个字段在 JSON 末尾）
        self.assertIn('{**obj, "service": SERVICE_ID}', backend)
        self.assertIn('TryGetProperty("service", out var service)', manager)

    def test_describe_site_states_the_kind_the_page_shows(self):
        described = source_config.describe_site({
            "name": "", "kind": "", "base_url": "https://torrents-csv.com",
            "web_search_url": "",
        })
        self.assertEqual(described["kind"], "torrents-csv")
        self.assertEqual(described["name"], "torrents-csv")
        self.assertEqual(described["base_url"], "https://torrents-csv.com/service/search")
        page = source_config.describe_site({
            "name": "搜索页", "kind": "", "base_url": "",
            "web_search_url": "https://s.example/s?q={q}",
        })
        self.assertEqual(page["kind"], "")
        self.assertEqual(page["name"], "s.example")
        self.assertEqual(page["web_search_url"], "https://s.example/s?q={q}")

    def test_the_config_page_keeps_the_element_ids_the_script_binds_to(self):
        """配置页的 id 是脚本与 DOM 之间的契约：改掉一个 id 就静默失去交互。"""
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        for element_id in ("jdl-media", "jdl-sites", "jdl-keywords", "jdl-port-value",
                           "jdl-backend-refresh", "jdl-backend-restart", "jdl-save"):
            self.assertIn('id="%s"' % element_id, page, "配置页缺少 %s" % element_id)
        # 一个站一个输入框：旧的四字段行（名称/类型/接口/网页）与暂存目录输入框不该留下
        self.assertNotIn('data-field="Kind"', page)
        self.assertNotIn('id="jdl-staging-input"', page)
        # 上移 / 下移 / 删除 / 新增四种按钮都要能被 syncList 认出来
        for action in ("'up'", "'down'", "'remove'", "'add'"):
            self.assertIn(action, page)
        self.assertIn("data-action", page)


if __name__ == "__main__":
    unittest.main()
