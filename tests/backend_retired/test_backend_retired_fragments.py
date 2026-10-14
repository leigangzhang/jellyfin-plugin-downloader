"""守卫自身的负向对照：拼出来的退役名字要能红，合法同名不同词不能红。

Subject: `_literal_offences` / `_prefix_offences` / `_fold_strings` 这三个扫描器
（都在 `tests/_backend_retired_helpers.py`）。
"""

from __future__ import annotations

import ast
import unittest

from tests.backend_retired._backend_retired_helpers import (
    _fold_strings,
    _literal_offences,
    _prefix_offences,
)


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


if __name__ == "__main__":
    unittest.main()
