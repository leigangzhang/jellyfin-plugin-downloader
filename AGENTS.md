# AGENTS.md — Jellyfin Downloader

## 本仓库是什么

一个 .NET 9 的 Jellyfin 插件（`Jellyfin.Plugin.JellyfinDownloader`），把「下载控制台」入口做进 Jellyfin 详情页。
插件自带一份 Python 后端（`backend/`，零第三方依赖），由插件进程托管在 `127.0.0.1` 上。

## 硬性约束（必须）

- **覆盖插件目录下的 DLL 之前必须先停 Jellyfin。** 运行中的进程会读到半写好的 DLL，
  报 `System.BadImageFormatException: Bad IL range`。用 `./install.sh`，它自己会先停服再覆盖。
- 改 `Web/inject.js` 或 `Web/style.css` 后必须重新 `./build.sh` 再 `./install.sh` 才生效——
  它们是 `EmbeddedResource`，内嵌进 DLL。
- `meta.json` 的 `version` 与 `Jellyfin.Plugin.JellyfinDownloader.csproj` 的 `<Version>` 必须一致。
- `backend/` 里**不允许出现机器相关路径**（媒体根、暂存区一律由插件经环境变量注入）。
- 后端只监听 `127.0.0.1`；插件入口仅管理员可用；密钥/令牌不入库。

## 约定（应该）

- 提交信息写清「改了什么 + 为什么」。
- 构建产物与运行时数据不入库：`out/`、`bin/`、`obj/`、`backups/`、`backend/state/` 已 gitignore。
- 改 `backend/` 下任何文件前，先读下面的「真相源」。

## 禁区

- 只做任务范围内的改动。
- 不把 `backend/state/` 或 `backups/` 提交进版本库。
- 不把机器相关的绝对路径写进 `backend/`（历史上出现过，已清理）。

## 套件入口

- **[docs/kit.md](docs/kit.md)**——套件是什么、三个采用分档、以及「去哪找什么」的路由表都在那一页；
  各家的归属索引在 [docs/README.md](docs/README.md)。
- 本仓库用的是 agent-workflow-kit 的**最小档**：十一站的进出口、每站的闸门与反模式在 [dev/README.md](dev/README.md)；
  装了哪几站在 [tools/tiers.json](tools/tiers.json)（发布、演化、退役、否决记录、事故复盘都声明为 `none`，别为它们造文件）。
- 写或改测试前读 [docs/testing.md](docs/testing.md)；写或改文档前读 [docs/documentation.md](docs/documentation.md)；
  给概念、检查或文档 kind 命名前读 [docs/glossary.md](docs/glossary.md)；把术语讲给没读过套件的人之前读
  [docs/plain-language.md](docs/plain-language.md)；改一份成对文档前读 [docs/i18n.md](docs/i18n.md)；
  动 `.agents/notes/` 前读 [.agents/notes/README.md](.agents/notes/README.md)。
- 常用命令在 [README.md](README.md#常用命令)：构建/安装/卸载，以及 `change-scope`、`run-evidence`、`check-invariants`、`gen-docs`、`pair-docs`、`unittest` 这些验收命令。
- 一处事实一个家：规则住在它链接的那份文件里，本文件只写规则与入口。你在意的约定要接进
  [tools/workflow.json](tools/workflow.json) 里一条会变红的检查——写不成检查的规则等于不存在。
- 闸门本身坏了就停下来说清楚（附上确切的调用），不要用 `--no-verify` 之类绕过去，也不要删掉检查的对象来求绿。

「通过」要说明它凭什么算证据：验收标准每条带编号并点名归属检查，新守卫要有人见过它红
（[apply-distrust](.agents/skills/apply-distrust/SKILL.md)、[pick-evidence](.agents/skills/pick-evidence/SKILL.md)）。


## 本仓库事实

| 项 | 值 |
|---|---|
| 插件安装目录（源码装） | `~/Library/Application Support/jellyfin/plugins/JellyfinDownloader_1.0.0.0/` |
| 插件安装目录（插件仓库装） | 同级的 `Jellyfin Downloader_1.0.0.0/`——Jellyfin 用**插件显示名**建目录，**带空格** |
| 插件 GUID | `b7f3e2a1-6c4d-4e9b-9a21-7d5c8e1f0a33` |
| 安装目录定位 | `install.sh` 按 `meta.json` 的 GUID 找已存在目录，避免同一插件装出两份 |
| 插件配置 | `~/Library/Application Support/jellyfin/plugins/configurations/Jellyfin.Plugin.JellyfinDownloader.xml` |
| 后端日志 | 同目录下 `jellyfin-downloader-backend.log` |
| 后端数据 | 插件目录下 `backend/state/`（`JMD_DATA_DIR` 可覆盖） |
| 后端注入的环境变量 | `JMD_MEDIA_ROOT`、`JMD_STAGING_ROOT` |
| 下载暂存 | 媒体根同盘的 `.staging/`（迅雷只允许写这里） |
| 自动 push | `post-commit` 钩子会 push 到上游；它**不进版本库**，重新 clone 后需重配 |

## 真相源

**`backend/` 与 skill `~/.agents/skills/jellyfin-movie-downloader/scripts/` 是两份独立代码，不是主从副本。**

2026-09-29 实测：14 个同名文件中 12 个字节一致、2 个有意分叉；另有 backend 独有的 2 个文件、skill 独有的 3 个文件。

- **数据格式以 `backend/` 为准。** 这一条不是外部规定，而是 `backend/media_download_lib.py`
  的注释里早就写下的决定：

  > 数据格式以**本插件这份脚本**为准：两边格式若出现冲突，按插件写出的格式走
  > （skill 侧那份只作历史基线，不再作为对齐目标）。

  本契约的作用，就是把这个已经做出的决定，从代码深处的注释提升到「开工就能读到」的地方。
- **有意分叉的两处**：`backend/media_download_lib.py`（backend 去掉了机器相关路径、改为自带数据根
  与同盘暂存推导）、`backend/watch_download.py`（docstring 跟着改）。
- **各自独有**：backend 侧是控制台 UI 相关（`backend/build_console.py`、`backend/console_server.py`）；
  skill 侧是 monitor_download.py、series_queue.py、thunder_bridge.py 三个文件。
- 改动任一侧时，请在提交信息里写明这次改的是哪一侧、是否影响另一侧。

## 已知坑位

- **卸载后按钮和图标还在**：Jellyfin 的卸载按钮只删文件，程序集仍在内存里；重启 Jellyfin 后消失。
  `./uninstall.sh` 加 `--verify-only` 可给确定结论。
- **日志反复出现拉图标失败**：本地插件仓库拉图标失败，纯外观问题，不影响功能。
- **面板提示后端未运行**：看配置页「服务端口」那行的运行状态徽标，点旁边的「刷新状态 / 重启后端」；端口被占用时插件自己往后换（页面只回显，不可改）。Python 没装或版本低于 3.9 后端起不来，装法见 README「快速开始」第 0 步。
- **测试覆盖是分层的**：取源打分链有离线契约测试（`tests/test_backend_scoring.py`、`tests/test_backend_sources.py`、`tests/test_backend_probe.py`，跑 `python3 -m unittest discover -s tests`）；下载/验证/归档路径仍靠 `./uninstall.sh` 的自检、Jellyfin 日志、面板快照三者交叉确认。
  （补齐下载/验证路径的自动化测试仍是推荐做法。）

## 发版

1. 改 `meta.json` 的 `version`，与 csproj 的 `<Version>` 保持一致
2. `./build.sh`
3. 打包 `JellyfinDownloader_<版本>.zip`：**zip 根目录**放 DLL、`meta.json`、`icon.png`、
   `backend/` 下的 .py（不要带 `backend/state`、`__pycache__`、日志）
4. `gh release create v<版本> JellyfinDownloader_<版本>.zip`
5. 更新根目录 `manifest.json`：`versions` 数组最前面加一条（含 `checksum`，用 `md5 -q <zip>` 算）
