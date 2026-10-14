"""跨模块共用的最小工具：字节分配量、路径包含判定、目录尾斜杠。

这里只放**不依赖包内其它模块**的叶子函数，避免 paths ← _shared 之外再出现环。
`media_download_lib` 对外可见的名字不变：`allocated_size` / `path_is_within` /
`ensure_trailing_slash` 由包的 `__init__` 与对应模块再导出，签名与行为逐字保持原样。
"""

import os


def allocated_size(path):
    stat = os.stat(path)
    return getattr(stat, "st_blocks", 0) * 512


def path_is_within(path, root):
    path = os.path.realpath(path)
    root = os.path.realpath(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def ensure_trailing_slash(path):
    return path if path.endswith(os.sep) else path + os.sep
