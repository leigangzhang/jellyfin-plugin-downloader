#!/usr/bin/env python3
"""`candidate_score` 的薄入口：只做再导出与 `__main__` 转发。

实现已经搬进同目录的 `candidate_score/` 包（policy / rules / release / speed /
score / cli），本文件在原路径上保留门面，让按文件路径加载它的调用方不受影响：

  * `tests/backend_probe/test_backend_scoring.py` 用
    `spec_from_file_location("jmd_candidate_score", <本文件>)` 加载本入口，
    不往 `sys.path` 插 `backend/`，随后直接取模块属性；
  * `python3 backend/candidate_score.py [--help|--show-weights]` 转发到包里的 CLI。

包内 import 需要 `backend/` 在 `sys.path` 上；按文件路径加载时它不在，所以下面先做
一次引导，再 import 包（与 a241dab 给 backend 子包入口立的先例一致）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from candidate_score import *  # noqa: E402,F401,F403
from candidate_score import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
