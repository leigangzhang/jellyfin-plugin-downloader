"""`backend/console_server/` —— 控制台服务的实现包（HTTP 面 / 搜索任务 / 快照 / 进程）。

入口是**同名的文件** `backend/console_server.py`：那条路径被外部契约钉死了
（插件的 BackendManager 按固定路径启动它、uninstall.sh 按 `console_server.py` 认这个
进程、测试按这条路径读源码做断言），而 Python 会把同名目录当成包 —— 也就是说
`import console_server` 默认拿到的是本文件（空的），不是入口。

所以本文件把入口的源码按 `console_server` 这个名字加载成真正的模块并顶替自己：
`import console_server` 与「`python3 backend/console_server.py`」看到的是同一份契约面
（常量、`_search_status`、service 信封、组合根），只有一份事实源。实现细节见各子模块。
"""
from __future__ import annotations

import importlib.util
import os
import sys

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENTRY_PATH = os.path.normpath(os.path.join(_PACKAGE_DIR, os.pardir, "console_server.py"))

# 先注册再执行：入口里的 `import console_server.http_api` 要靠这一条才能在包自身
# 初始化期间找到子模块（此时 `console_server` 已经是下面的 _entry，它的 __path__
# 指向本目录）。importlib 的 _load() 在 exec 之后会把 sys.modules 里现有的对象
# （= _entry）移到末尾并返回它，所以 `import console_server` 得到的就是入口模块。
_spec = importlib.util.spec_from_file_location(
    __name__, _ENTRY_PATH, submodule_search_locations=[_PACKAGE_DIR])
_entry = importlib.util.module_from_spec(_spec)
sys.modules[__name__] = _entry
_spec.loader.exec_module(_entry)
