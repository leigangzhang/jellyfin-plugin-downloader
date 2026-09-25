# Jellyfin Downloader

把「找资源 → 打分 → 交给迅雷下载 → 校验 → 归档进媒体库」整条链路，搬进 Jellyfin 自己的详情页。

装好之后，电影 / 剧集 / 单集详情页会多出一个 **「获取资源」** 按钮（仅管理员可见）。点一下：插件从多个来源收集候选（磁力 + 迅雷云盘分享），按统一权重打分排序，列出达到阈值的结果；你挑一个提交，剩下的（下载监控、六层校验、规范命名、归档到媒体库、刷新媒体库）都由后端自动完成。全程不用离开 Jellyfin。

> 这是一个"真" .NET 插件（`net9.0`，targetAbi `10.11.11.0`），**自带一份零第三方依赖的 Python 后端**。后端脚本是从作者本人的下载 skill 里剥离出来的自包含副本，随插件目录走，不依赖任何外部 skill 或额外安装。

---

## 功能一览

| 能力 | 说明 |
|---|---|
| 详情页入口 | 电影 / 剧集 / 单集页面的「获取资源」按钮，仅管理员可见（前端隐藏 + 后端校验双重把关） |
| 多来源候选 | 按优先级逐级降级：结构化搜索 API → 浏览器发现 → 本地 Pansou 实例（可选）；磁力与迅雷云盘分享同池比较 |
| 统一打分 | `100 × (0.34·画质 + 0.28·下载速度 + 0.26·观看体验 + 0.12·适配成本)`，候选全量排名，**不按规则硬淘汰** |
| 面板决策 | 面板里列出每个候选的总分与四维拆解（画质/速度/体验/适配）、体积、码率、字幕、片源，可调最低分阈值 |
| 分季抓取 | 剧集页先拉季列表；选定季后再抓，快照按 `<剧名>.sNN.*.json` 分季存放，切季不重复搜索 |
| 严格按季 | 默认开启：搜索只带季关键词，并隐藏没标季号的候选与同名噪音条目 |
| 实测速度 | 可对头部候选做短测，把实测吞吐写回分数后重新排名（实测只影响"速度"分，不越过画质与体验） |
| 提交与监控 | 提交到迅雷、在线改保存路径与选片、测真实 ETA，不合格只清理本次任务；合格则启动后台 watcher |
| 六层校验 | `ffprobe` 流信息 → 三段抽样解码 → 集号/残片/重复大文件 → Jellyfin 数据库对账 → 临时文件与结构卫生 → 硬字幕启发式 |
| 自动归档 | 校验通过后按 `剧名 S01E01.ext` / `片名 (年份).ext` 规范命名，同盘原子移动到媒体库并触发库刷新 |
| 云盘通道 | 迅雷云盘分享链接作为一等候选：转存前按标题粗排，转存后用本机云盘缓存里的真实文件清单精排 |

---

## 它是怎么工作的

```
┌──────────────────────── Jellyfin（8096）────────────────────────┐
│  jellyfin-web/index.html                                        │
│      ▲ 请求时由中间件追加 <script>/<link>                        │
│      │                                                          │
│  JellyfinDownloaderController                                   │
│    /JellyfinDownloader/script|style     内嵌的 inject.js / css  │
│    /JellyfinDownloader/config|seasons   配置与季列表            │
│    /JellyfinDownloader/backend/*        后端进程管理            │
│    /JellyfinDownloader/python/*         Python 探测与安装       │
│    /JellyfinDownloader/api/{**path} ────┐ 同源代理，无需 CORS   │
│    /JellyfinDownloader/manifest.json    └ 可选：本地插件仓库    │
└─────────────────────────────────────────┼───────────────────────┘
                                          ▼
                      Python 后端 127.0.0.1:8123（仅本机）
                      console_server.py（标准库 http.server，零依赖）
                        /api/search      建候选池 + 打分排名
                        /api/snapshot    面板快照（任务/候选/标记）
                        /api/submit      提交迅雷
                        /api/watch       起 watcher（下载→校验→归档）
                        /api/verify      手工复验
                        /api/cleanup     清理残留
                        /api/pan/*       云盘转存/取回状态机
                        /api/mark        人工标记
                        /api/publish     面板产物发布/回滚
                                          │
                        后端脚本（backend/*.py）│
                        ├─ 迅雷数据库 / 云盘缓存（只读）
                        ├─ aria2c（种子元数据、测速）
                        └─ ffprobe/ffmpeg（媒体验证）
```

要点：

- **前端注入不依赖别的插件**。插件自己注册 ASP.NET 中间件改写 `/web/index.html`，把 `<script>`/`<link>` 追加进去（所以不需要装 File Transformation）。
- **后端只绑 `127.0.0.1`**，浏览器侧一律经 Jellyfin 同源代理访问，因此没有 CORS 问题，也不用给后端单独做鉴权。
- **后端随 Jellyfin 生命周期起停**：插件加载即拉起后端，Jellyfin 优雅退出时把后端及其子进程一起收掉。
- **写操作都落在后端脚本里**，后端自身不改迅雷数据库，所有下载先落 `.staging`，校验通过才进媒体库。

---

## 环境要求

| 组件 | 说明 |
|---|---|
| Jellyfin | 10.11.x（`targetAbi 10.11.11.0`，实测 v10.11.11） |
| 操作系统 | **macOS**。迅雷客户端数据库、"下载到本地"、云盘缓存等路径都是 macOS 专属 |
| Python | **3.9+**，后端只用标准库。插件会自动探测（配置项 → PATH → 常见安装位置），也可以手工指定或一键安装 |
| .NET SDK | **9.0**，仅构建时需要。`build.sh` 默认使用 Homebrew 的 `dotnet@9` |
| 迅雷（Thunder） | 桌面客户端，用于提交下载与云盘转存/取回 |
| aria2c | 抓种子元数据与实测速度：`brew install aria2` |
| ffmpeg / ffprobe | 媒体验证与抽样解码：`brew install ffmpeg` |

---

## 快速开始

```bash
git clone git@github.com:leigangzhang/jellyfin-plugin-downloader.git
cd jellyfin-plugin-downloader

./build.sh      # dotnet publish → ./out（需要 .NET 9 SDK）
./install.sh    # 安装到 Jellyfin 插件目录并重启 Jellyfin
```

`install.sh` 会：先停掉 Jellyfin → 复制 DLL / `meta.json` / `icon.png` / `backend/` → 保留原有的 `backend/state/`（历史快照与标记不丢）→ `open -n /Applications/Jellyfin.app` 重新拉起。

> 之所以先停服再覆盖：运行中的进程可能在复制途中读到半个 DLL，触发 `BadImageFormatException: Bad IL range`。

### 也可以直接在 Jellyfin 里安装（走本仓库的插件仓库）

仓库根目录的 `manifest.json` 就是一份标准的 Jellyfin 插件仓库清单，把它加进 Jellyfin 即可在插件目录里安装/更新：

```
https://raw.githubusercontent.com/leigangzhang/jellyfin-plugin-downloader/main/manifest.json
```

控制台 → 插件 → **仓库** → 添加上面这个地址 → 在插件目录里找到 **Jellyfin Downloader** → 安装 → 按提示重启 Jellyfin。这条路径不需要本机装 .NET SDK，安装包由 [Releases](https://github.com/leigangzhang/jellyfin-plugin-downloader/releases/latest) 提供（`manifest.json` 里的 `sourceUrl` + `checksum` 已填好）。

> 两条路径等价：源码构建适合改完代码自测，插件仓库适合装在别的机器上或图省事。

**首次配置**：控制台 → 插件 → **Jellyfin Downloader**

| 配置项 | 默认 | 说明 |
|---|---|---|
| 端口 | `8123` | 后端监听端口（只绑本机） |
| Python 解释器 | 留空 | 留空 = 自动探测；也可填绝对路径，或用「安装 / 更换 Python」按钮 |
| 媒体目录 | 留空 | 留空 = 由插件从 Jellyfin 媒体库配置解析出库根 |
| 暂存目录 | 留空 | 留空 = 媒体根同盘的 `.staging`（保证归档能同盘原子移动） |

**开始使用**：进入任意电影 / 剧集 / 单集详情页 → 点 **「获取资源」**。

- 剧集页会先列出季；选定季再点「重新获取」才会抓取（换季读各自的快照，不重复搜索）。
- 面板里的 **最低分阈值** 默认取插件配置的 `MinScore`（60），可随时调整。
- **严格按季**（默认开）只搜当前季并过滤无季号 / 同名噪音；**含整季包**（默认关）额外纳入整季合集的候选。

---

## 卸载

```bash
./uninstall.sh              # 清理插件文件，全程不停服、不重启
./uninstall.sh --verify-only  # 只自检，不改任何东西（重启 Jellyfin 后再跑一次复核）
./uninstall.sh --dry-run      # 只打印将要做什么
```

Jellyfin 后台的「卸载」按钮**只删插件目录**，剩下的收尾（配置文件、后端日志、仍在跑的 Python 后端）得自己处理。`uninstall.sh` 做的就是这件事，而且刻意**不碰 Jellyfin 进程**：

1. 把改前内容留档到 `backups/jellyfin-downloader-uninstall-<时间戳>/`（含插件目录与 `backend/state`、被删的配置、改前的 `system.xml`，附 `MANIFEST.sha256`）；
2. 删插件目录、插件配置 XML、后端日志；
3. 回收插件自己拉起的 Python 后端进程（`--keep-backend` 可保留）；
4. 自检文件层面是否真的清干净，并如实报告运行态残留。

关于「本地插件仓库」条目：运行中的 Jellyfin 配置在内存里，手改 `system.xml` 会被内存副本覆盖回去，所以脚本在 Jellyfin 运行时不改这个文件，而是提示你 **控制台 → 插件 → 仓库 → 删掉 `Jellyfin Downloader (local)`**（无需重启）；若 Jellyfin 已停，可用 `--clean-repo-entry` 让脚本代劳。

常用开关：`--purge-data`（运行数据不留档）、`--purge-legacy`（连旧 skill 侧数据一起删，先留档）、`--no-backup`、`--clean-repo-entry`。

> 由于不重启，卸载后当前运行的实例仍持有已加载的程序集（按钮与 `/JellyfinDownloader/*` 端点还在），**下次重启 Jellyfin 后自动消失**；届时用 `./uninstall.sh --verify-only` 复核即可。

---

## 目录结构

```
jellyfin-plugin-downloader/
├── Jellyfin.Plugin.JellyfinDownloader/     # C# 插件（net9.0）
│   ├── Plugin.cs                           # 插件入口
│   ├── BackendManager.cs                   # 后端进程的启动/停止/重启（含按端口兜底）
│   ├── BackendHostedService.cs             # 随 Jellyfin 生命周期起停后端
│   ├── PythonLocator.cs / PythonInstaller.cs  # 探测与安装 Python
│   ├── MediaPaths.cs                       # 从媒体库配置解析媒体根 / 暂存区
│   ├── Controllers/JellyfinDownloaderController.cs  # 全部 HTTP 端点 + 后端代理
│   ├── Middleware/                         # 往 /web/index.html 注入脚本与样式
│   ├── Web/inject.js, Web/style.css        # 面板前端（内嵌资源）
│   └── Configuration/                      # 配置项与配置页
├── backend/                                # 自带 Python 后端（自包含，零第三方依赖）
│   ├── console_server.py                   # HTTP 服务 + 面板快照
│   ├── build_console.py                    # 快照组装
│   ├── candidate_score.py                  # 打分（四维权重）
│   ├── search_pool.py / probe_magnets.py   # 候选来源与打分排名
│   ├── pan_search.py / pan_pool.py / pan_transfer.py  # 云盘分享：找链接/入池/转存取回
│   ├── speed_probe.py / submit_xunlei.py   # 实测速度 / 提交迅雷
│   ├── watch_download.py / verify_media.py / handle_media_issues.py / cleanup_download.py
│   ├── check_exists.py / media_download_lib.py
│   └── state/                              # 运行时数据（不入库）：pool / watches / snapshots / pan / marks
├── manifest.json                           # Jellyfin 插件仓库清单（仓库根目录即插件仓库）
├── build.sh                                # 构建 → ./out
├── install.sh                              # 安装到 Jellyfin 并重启
├── uninstall.sh                            # 干净卸载（不停服、不重启）
└── meta.json, icon.png                     # 插件元数据与图标
```

**运行时文件位置**

| 内容 | 路径 |
|---|---|
| 插件本体 | `~/Library/Application Support/jellyfin/plugins/JellyfinDownloader_1.0.0.0/` |
| 插件配置 | `…/jellyfin/plugins/configurations/Jellyfin.Plugin.JellyfinDownloader.xml` |
| 后端数据 | `…/JellyfinDownloader_1.0.0.0/backend/state/`（候选池、watcher、快照、云盘、人工标记） |
| 后端日志 | `…/plugins/configurations/jellyfin-downloader-backend.log` |
| 下载暂存 | 媒体根同盘的 `.staging/`（迅雷只允许写这里） |
| 卸载留档 | `backups/jellyfin-downloader-uninstall-<时间戳>/`（已 gitignore） |

---

## 常见问题

**装完在详情页看不到按钮？**
先硬刷新（`Cmd+Shift+R`）排掉浏览器缓存；确认当前账号是管理员；再看日志里有没有 `Loaded assembly "Jellyfin.Plugin.JellyfinDownloader…"`。

**安装时报 `BadImageFormatException: Bad IL range`？**
运行中的 Jellyfin 读到了半写的 DLL。用 `./install.sh`（会先停服），或先手动停掉 Jellyfin 再覆盖文件。

**卸载了，按钮和图标还在？**
Jellyfin 的卸载按钮只删文件，程序集仍在内存里。重启 Jellyfin 后即消失；`./uninstall.sh --verify-only` 可以给出确定结论。

**日志里反复出现 `Failed to download image to path ".../JellyfinDownloader_1.0.0.0/Image"`？**
本地插件仓库拉图标失败，纯外观问题，不影响任何功能。

**面板提示后端未运行？**
看配置页的 Python 解释器与端口；日志在 `plugins/configurations/jellyfin-downloader-backend.log`。端口被占用时换个端口，插件会自动重启后端。

**没装 Python？**
配置页有「安装 / 更换 Python」按钮；也可以自己 `brew install python`，插件会自动探测到。

**想让它出现在 Jellyfin 的插件目录里？**
两个地址都能用：
- **公开插件仓库**（推荐，插件还没装时也能用）：`https://raw.githubusercontent.com/leigangzhang/jellyfin-plugin-downloader/main/manifest.json`
- **插件自带的本地端点**（插件装好之后才有）：`http://127.0.0.1:8096/JellyfinDownloader/manifest.json`

控制台 → 插件 → 仓库 → 添加其一即可，之后可在 Jellyfin 内安装与更新。不想要就随时删掉这条仓库；卸载插件时也建议一并删除，否则每次启动都会去拉一个已失效的 manifest（`./uninstall.sh` 会提示你）。

---

## 开发说明

- **前端**：改 `Jellyfin.Plugin.JellyfinDownloader/Web/inject.js` 或 `style.css`（以 `EmbeddedResource` 内嵌进 DLL），必须重新 `./build.sh && ./install.sh` 才生效。
- **打分**：四维权重在 `backend/candidate_score.py` 的 `DEFAULT_WEIGHTS`；改完只需重装后端（`install.sh` 会带上 `backend/`）。
- **后端边界**：`backend/` 是随插件走的自包含副本，数据根固定为 `backend/state/`（可用 `JMD_DATA_DIR` 覆盖），与作者本机的下载 skill 完全隔离，互不读写。
- **后端环境变量**：由插件注入 `JMD_MEDIA_ROOT`（媒体库根）与 `JMD_STAGING_ROOT`（暂存区），脚本读不到时才回落到默认值。
- **手工验证**：目前没有自动化测试，靠 `./uninstall.sh --verify-only`、Jellyfin 日志、面板快照三者交叉确认。

### 发一个新版本

1. 改 `meta.json` 的 `version`（与 `csproj` 里的 `<Version>` 保持一致）；
2. `./build.sh`；
3. 打包：`JellyfinDownloader_<版本>.zip`，**zip 根目录**放 `Jellyfin.Plugin.JellyfinDownloader.dll`、`meta.json`、`icon.png` 和 `backend/*.py`（不要带 `backend/state`、`__pycache__`、`*.log`）；
4. `gh release create v<版本> JellyfinDownloader_<版本>.zip`；
5. 更新根目录 `manifest.json`：在 `versions` 数组最前面加一条新的 `{version, changelog, targetAbi, sourceUrl, checksum(md5), timestamp}`（`checksum` 用 `md5 -q <zip>`）。

---

## 说明

个人自用项目，未附开源许可证。插件入口仅管理员可用，后端只监听 `127.0.0.1`。
