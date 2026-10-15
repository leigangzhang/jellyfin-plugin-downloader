"""跨模块共用的最小工具：外部工具路径解析、字节分配量、路径包含判定、目录尾斜杠。

这里只放**不依赖包内其它模块**的叶子函数，避免 paths ← _shared 之外再出现环。
`media_download_lib` 对外可见的名字不变：`allocated_size` / `path_is_within` /
`ensure_trailing_slash` 由包的 `__init__` 与对应模块再导出，签名与行为逐字保持原样；
`tool_path` / `ToolMissing` / `TOOL_ENV` / `INSTALL_HINTS` 是 P0 新增的名字，
同样由包的 `__init__` 再导出。

外部工具路径（P0）
------------------
Jellyfin 从 Finder / `open` 启动时，插件后端继承的是 launchd 的最小 PATH，
**不包含 /opt/homebrew/bin**。此前后端按裸名调用 aria2c / ffprobe / ffmpeg，于是
`FileNotFoundError`（⊂ OSError）被

    except (OSError, subprocess.SubprocessError, ValueError): return []

吞掉，最终上报成 `RESOURCE_REJECTED no readable video >=100MB found in torrent`
—— 环境故障伪装成了「这个种子里没有可读视频」，实测耗时 105–228ms。

两段式分工（与插件侧一致）：
  * 安装期：`install.sh` 确保三个工具存在，并把绝对路径记进
    `<插件安装目录>/backend-tools.json`；
  * 运行期：插件 `BackendManager` 读同一份记录，把解析结果经 `JMD_*_PATH`
    注入后端；`tool_path()` 是后端侧的读取处。

解析顺序：`JMD_*_PATH`（插件注入）→ PATH 上的裸名。`JMD_*_PATH` 指向不可执行的东西时
**直接报错、不静默回退**：那条变量是插件按记录文件填的，它坏了就是要让人看见，
而不是让探测静默换一个来源继续跑。工具缺失一律抛 `ToolMissing`（⊂ RuntimeError，
**不是** OSError），所以上面那种 `except (OSError, ...)` 再也吞不到它。
"""

import os
import shutil

# 工具名 → 注入该工具绝对路径的环境变量名。
# 这两个名字必须与 C# 侧 ToolLocator.EnvVar 和 BackendManager 的注入处逐字相同，
# tests/backend_probe/test_tool_paths.py 盯着这条约定。
TOOL_ENV = {
    "aria2c": "JMD_ARIA2C_PATH",
    "ffprobe": "JMD_FFPROBE_PATH",
    "ffmpeg": "JMD_FFMPEG_PATH",
}

# 缺失时告诉用户怎么装上（macOS / Homebrew；Linux 上包名相同）。
INSTALL_HINTS = {
    "aria2c": "brew install aria2",
    "ffprobe": "brew install ffmpeg",
    "ffmpeg": "brew install ffmpeg",
}


class ToolMissing(RuntimeError):
    """外部工具缺失（或注入的路径不可用）。

    `name` 是工具名（`aria2c` / `ffprobe` / `ffmpeg`），`install_hint` 是安装命令。
    刻意继承 RuntimeError 而不是 OSError：既有的 `except OSError` 兜底是给
    「工具在、这一轮网络/参数出问题」用的，工具缺失不该被它归类。
    """

    def __init__(self, name, *, reason="not found"):
        self.name = name
        self.install_hint = INSTALL_HINTS.get(name, "")
        env_name = TOOL_ENV.get(name, "")
        detail = f"{name} {reason}"
        if env_name:
            detail += f" ({env_name})"
        if self.install_hint:
            detail += f"; install it with: {self.install_hint}"
        super().__init__(detail)


def tool_path(name):
    """返回 `name` 的绝对路径。

    顺序：`JMD_*_PATH` → PATH 上的裸名。两处都没有就抛 `ToolMissing`。
    注入的路径存在但不可执行时也抛 `ToolMissing`（不静默改用 PATH 上的同名命令）。
    """
    if name not in TOOL_ENV:
        raise ValueError(f"unknown tool: {name}")

    injected = (os.environ.get(TOOL_ENV[name]) or "").strip()
    if injected:
        if os.path.isfile(injected) and os.access(injected, os.X_OK):
            return injected
        raise ToolMissing(name, reason=f"is not executable: {injected}")

    found = shutil.which(name)
    if found:
        return found

    raise ToolMissing(name)


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
