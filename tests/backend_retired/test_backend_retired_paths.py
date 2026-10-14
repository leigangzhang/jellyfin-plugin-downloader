"""退役通道的符号守卫：`backend/` 里不许再出现它们的名字或常量。

Subject: `backend/*.py` 的模块表面（AST 扫描），另外钉住测速模块本身没有回来、
`console_server.py` 里没有手动的 `/api/verify` 端点。

判定走 AST：**符号、关键字参数名、import 的模块名、当作常量出现的 CLI flag 与字典键**。
注释和 docstring 允许点名这些通道（那是写给下一个读者的「别再把它加回来」），所以不按整段
文本匹配。共用夹具与扫描器在 `tests/_backend_retired_helpers.py`。

本模块原来装着全部五个主题，2026 年按主题拆开后只留下退役符号这一支；
地址分类在 `test_backend_retired_site_url.py`，页面与 C# 的源码守卫在
`test_backend_retired_backend_contract.py` 与 `test_backend_retired_config_page.py`。
"""

from __future__ import annotations

import ast
import unittest

from tests.backend_retired._backend_retired_helpers import (
    BACKEND,
    RETIRED_SYMBOLS,
    _fold_strings,
    _literal_offences,
    _prefix_offences,
    backend_modules,
)


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


if __name__ == "__main__":
    unittest.main()
