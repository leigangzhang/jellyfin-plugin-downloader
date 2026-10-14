"""console_server 侧的接线，用源码文本断言（导入它会拉起整个控制台栈）。

拆自 `tests/backend_probe/test_backend_probe.py`（按主题），共享件在
`tests/backend_probe/_backend_probe_helpers.py`。"""

from __future__ import annotations

import unittest

from tests.backend_probe._backend_probe_helpers import (
    BACKEND,
)

class ConsoleWiringTests(unittest.TestCase):
    """console_server 侧的接线，用源码断言（导入它会拉起整个控制台栈）。"""

    def setUp(self):
        self.source = (BACKEND / "console_server.py").read_text(encoding="utf-8")

    def test_console_passes_the_probe_budget_and_no_speed_probe(self):
        self.assertNotIn("speed-probe", self.source)
        self.assertIn('"--budget-secs"', self.source)
        self.assertIn("PROBE_BUDGET_SECS", self.source)

    def test_console_budget_stays_below_the_round_timeout(self):
        namespace = {}
        for line in self.source.splitlines():
            match = __import__("re").match(r"(SEARCH_TIMEOUT|PROBE_BUDGET_SECS) = (\d+)", line)
            if match:
                namespace[match.group(1)] = int(match.group(2))
        self.assertIn("SEARCH_TIMEOUT", namespace)
        self.assertIn("PROBE_BUDGET_SECS", namespace)
        self.assertLess(namespace["PROBE_BUDGET_SECS"], namespace["SEARCH_TIMEOUT"])

    def test_exit_code_mapping_tokens_are_unchanged(self):
        """0/4/5/7/8 的语义由这些 token 与 _search_status 决定，不能被改动带走。

        `UNSUPPORTED_THUNDER_DIRECT_LINK` 已从这份清单里去掉：它的唯一生产者是
        `probe_magnets --magnet`（连同 rc=3 一起删除），控制台里再留这条映射就是死代码，
        而它映射的正是本用例没列的 rc=3。解析不出 info hash 的行现在由
        `CANDIDATE_LINES_SKIPPED` 报数，不再静默（复核报告 F6）。
        """
        for token in (
            "SWARM_DEAD", "METADATA_FAILED", "NO_ELIGIBLE_RESOURCE",
            "NO_CANDIDATES", "BEST_BELOW_MIN_SCORE", "BEST_DEAD_SWARM",
        ):
            self.assertIn(token, self.source, token)
        self.assertIn('return "best_dead_swarm"', self.source)
        self.assertNotIn("UNSUPPORTED_THUNDER_DIRECT_LINK", self.source)

    def test_probe_still_exposes_the_same_exit_codes(self):
        # 0/2 在 probe_magnets.main() 的入口分支里，4/5/7/8 在 probe_round 的交付阶段。
        # 两个文件合起来必须仍然覆盖 0/4/5/7/8；把范围钉在两个文件上，语义守卫不变。
        probe_source = "\n".join(
            (BACKEND / name).read_text(encoding="utf-8")
            for name in ("probe/probe_magnets.py", "probe/probe_round.py")
        )
        for code in ("return 0", "return 4", "return 5", "return 7", "return 8"):
            self.assertIn(code, probe_source, code)

