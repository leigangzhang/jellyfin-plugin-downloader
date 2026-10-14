"""`backend/console_server/` —— 控制台服务的实现包（HTTP 面 / 搜索任务 / 快照 / 进程）。

入口是**同名的文件** `backend/console_server.py`：那条路径被外部契约钉死了
（插件的 BackendManager 按固定路径启动它、uninstall.sh 按 `console_server.py` 认这个
进程、测试按这条路径读源码做断言），而 Python 会把同名目录当成包 —— 也就是说
`import console_server` 默认拿到的是本文件（空的），不是入口。

所以本文件让 `console_server` 这个名字指向**入口模块本身**，让两条路线共用同一个
模块对象（同一份常量、同一个 Handler、同一个 `_wire_http_api`）：

  * 入口正以脚本方式运行时，那个模块就是 `sys.modules["__main__"]` —— 直接别名过去，
    **不能再执行一遍**：执行两遍的话，入口自己定义的那些符号（Handler / _search_status /
    main / build_search_cmd / _dispatch_search / _wire_http_api …）会各来一份，组合根那次
    接线也就有两份。别名之后，入口的符号与 `_wire_http_api()` 在任何一条路线下都恰好
    定义/执行一次。
  * 其余情形（被 import、`-c`、runpy）按路径把入口执行成 `console_server`。

两条路线都从包内子模块取实现（`console_server.jobs` 等），共享状态留在那些叶子模块里。
"""
from __future__ import annotations

import importlib.util
import os
import sys

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENTRY_PATH = os.path.normpath(os.path.join(_PACKAGE_DIR, os.pardir, "console_server.py"))


def _entry_running_as_main():
    """入口是不是正以 `__main__` 运行？是则返回那个模块，否则返回 None。

    判据与真脚本方式一致：`sys.modules["__main__"].__file__` 就是入口文件。
    先绝对化 + normpath 比一次，再按 realpath 比一次兜符号链接（macOS 上 /tmp 就是
    /private/tmp）；`-c` / 交互式下 `__main__` 没有 `__file__`，直接判否。
    """
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if main_module is None or not main_file:
        return None
    if os.path.normpath(os.path.abspath(main_file)) == _ENTRY_PATH:
        return main_module
    if os.path.realpath(main_file) == os.path.realpath(_ENTRY_PATH):
        return main_module
    return None


_main_module = _entry_running_as_main()
if _main_module is not None:
    # 脚本方式：别名过去，入口不再执行第二遍。__path__ 指向本目录 —— 入口里的
    # `from console_server.http_api import …` 要靠它才认得出这是个包
    #（否则是 “console_server is not a package”）。
    _main_module.__path__ = [_PACKAGE_DIR]
    sys.modules[__name__] = _main_module
else:
    # 先注册再执行：入口里的 `import console_server.http_api` 要靠这一条才能在包自身
    # 初始化期间找到子模块（此时 `console_server` 已经是下面的 _entry，它的 __path__
    # 指向本目录）。importlib 的 _load() 在 exec 之后会把 sys.modules 里现有的对象
    # （= _entry）移到末尾并返回它，所以 `import console_server` 得到的就是入口模块。
    _spec = importlib.util.spec_from_file_location(
        __name__, _ENTRY_PATH, submodule_search_locations=[_PACKAGE_DIR])
    _entry = importlib.util.module_from_spec(_spec)
    sys.modules[__name__] = _entry
    _spec.loader.exec_module(_entry)
