"""已退役通道守卫的共享夹具：常量、AST/字符级扫描器、路径与端口助手。

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

这里放着各主题模块共用的东西：退役名单（`RETIRED_SYMBOLS` / `RETIRED_CONSTANTS` /
`NEEDLES`）、扫描器（`_literal_offences` / `_prefix_offences` / `_fold_strings` /
`_skip_string` / `anonymous_object_keys`）、扫描面（`ROOT` / `BACKEND` /
`backend_modules`）与 `pick_free_port`。导入本模块等于把 `backend/` 挂上 `sys.path`
并再导出 `source_config`；夹具与网络都无关，这里只读仓库里的源文件。

`tests/harness.py` 不在这里用：它加载的是套件自己的工具，而这一族模块的被测对象是
插件后端，那个根之外的代码（同 `tests/test_backend_sources.py` 的理由）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

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


if __name__ == "__main__":
    raise SystemExit("这不是测试模块；用例在同目录的 test_backend_retired_*.py 里。")
