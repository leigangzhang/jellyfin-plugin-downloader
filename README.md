# Jellyfin Downloader

把「找资源 → 打分 → 交给迅雷下载 → 校验 → 归档进媒体库」整条链路，搬进 Jellyfin 自己的详情页。

装好之后，电影 / 剧集 / 单集详情页会多出一个 **「获取资源」** 按钮（仅管理员可见）。点一下：插件从多个来源收集候选（磁力 + 迅雷云盘分享），按统一权重打分排序，列出达到阈值的结果；你挑一个提交，剩下的（下载监控、六层校验、规范命名、归档到媒体库、刷新媒体库）都由后端自动完成。全程不用离开 Jellyfin。

右上角另有 **「热门」** 入口（同样仅管理员可见）：列出 TMDb 的本周热门与流行电影 / 剧集，选中一个标题直接落进同一套打分流程；点「关注」可以把还没下载的条目先在媒体库占位（默认关闭，见配置页「热门」组）。

> 这是一个"真" .NET 插件（`net9.0`，targetAbi `10.11.11.0`），**自带一份零第三方依赖的 Python 后端**。后端脚本是从作者本人的下载 skill 里剥离出来的自包含副本，随插件目录走，不依赖任何外部 skill 或额外安装。

---

## 功能一览

| 能力 | 说明 |
|---|---|
| 详情页入口 | 电影 / 剧集 / 单集页面的「获取资源」按钮，仅管理员可见（前端隐藏 + 后端校验双重把关） |
| 可配置资源站 | 资源站清单是插件配置（每行一个站点地址，默认保留原有两个站），后端逐个搜索；加一个站不用改代码 |
| 发布者关键字优先 | 配置的关键字组合（默认「高清剧集网」「DDHDTV」）与片名拼成查询，**排在搜索阶梯最前**，严格按季时同样跑 |
| 统一打分 | `100 × (0.34·画质 + 0.28·下载速度 + 0.26·观看体验 + 0.12·适配成本)`，候选全量排名，**不按规则硬淘汰**；下载速度这一维只由做种者数（与外部索引快照）决定 |
| 面板决策 | 面板里列出每个候选的总分与四维拆解（画质/速度/体验/适配）、体积、码率、字幕、片源，可调最低分阈值 |
| 分季抓取 | 剧集页先拉季列表；选定季后再抓，快照按 `<剧名>.sNN.*.json` 分季存放，切季不重复搜索 |
| 严格按季 | 默认开启：搜索只带季关键词；**明确标了别的季**的候选在服务端就被丢掉（认 `SxxEyy`、裸 `Sxx`、`第N季`·`第X季`、`Season N`），没标季号的候选留给面板折叠；同名噪音条目由面板隐藏 |
| 提交与监控 | 提交到迅雷、在线改保存路径与选片、测真实 ETA，不合格只清理本次任务；合格则启动后台 watcher |
| 六层校验 | `ffprobe` 流信息 → 三段抽样解码 → 集号/残片/重复大文件 → Jellyfin 数据库对账 → 临时文件与结构卫生 → 硬字幕启发式 |
| 自动归档 | 校验通过后按 `剧名 S01E01.ext` / `片名 (年份).ext` 规范命名，同盘原子移动到媒体库并触发库刷新 |
| 云盘通道 | 站点地址里带 `{q}` 占位符的那些（插件配置）会被当成该站的搜索页，抓出迅雷云盘分享链接作为一等候选：转存前按标题粗排，转存后用本机云盘缓存里的真实文件清单精排 |
| 热门入口 | 右上角「热门」列出 TMDb 的本周热门 / 流行电影与剧集（配置页填 TMDb API Key 后生效，无 key 时只显示配置引导）；选中标题直接落进打分流程 |
| 关注占位 | 「关注」把还没下载的条目记进 `backend/state/trending/`；打开 `AnchorEnabled` 后还会在媒体库里写一个占位文件，让它在下载落地前就有库页（默认关闭 = 对媒体库零写入） |

---

## 它是怎么工作的

```
┌──────────────────────── Jellyfin（8096）────────────────────────┐
│  jellyfin-web/index.html                                        │
│      ▲ 请求时由中间件追加 <script>/<link>                        │
│      │                                                          │
│  JellyfinDownloaderController                                   │
│    /JellyfinDownloader/script|style     内嵌的 inject 分片/css  │
│    /JellyfinDownloader/config|seasons   配置与季列表            │
│    /JellyfinDownloader/trending/*       热门查库 / 库内占位      │
│    /JellyfinDownloader/backend/*        后端进程管理            │
│    /JellyfinDownloader/python/*         Python 探测与安装       │
│    /JellyfinDownloader/api/{**path} ────┐ 同源代理，无需 CORS   │
│    /JellyfinDownloader/manifest.json    └ 可选：本地插件仓库    │
└─────────────────────────────────────────┼───────────────────────┘
                                          ▼
                      Python 后端 127.0.0.1:8123（仅本机）
                      console_server.py（标准库 http.server，零依赖）
                        /api/search      建候选池 + 打分排名（资源站逐个搜 + 云盘网页搜索）
                        /api/search/status  轮询这一轮的进度
                        /api/snapshot    面板快照（本轮状态/候选/任务）
                        /api/submit      提交迅雷
                        /api/watch       起 watcher（下载→校验→归档）
                        /api/pan/*       云盘取回状态机
                        /api/trending    TMDb 榜单（TTL 缓存 + 过期降级）
                                          │
                        后端脚本（backend/*.py）│
                        ├─ 迅雷数据库 / 云盘缓存（只读）
                        ├─ aria2c（种子元数据）
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
| Python | **3.9+**，后端只用标准库。插件自动探测：PATH 上的 `python3` → 常见安装位置（Homebrew / python.org 框架 / pyenv / conda / uv）。装了命令行开发者工具时系统那份 `/usr/bin/python3` 通常就够（本机实测 3.9.6）。**探测不到后端起不来**，所以先按「快速开始」第 0 步装好 |
| .NET SDK | **9.0**，仅构建时需要。`build.sh` 默认使用 Homebrew 的 `dotnet@9` |
| 迅雷（Thunder） | 桌面客户端，用于提交下载与云盘转存/取回 |
| aria2c | 抓种子元数据：`brew install aria2` |
| ffmpeg / ffprobe | 媒体验证与抽样解码：`brew install ffmpeg` |

---

## 快速开始

```bash
git clone git@github.com:leigangzhang/jellyfin-plugin-downloader.git
cd jellyfin-plugin-downloader

# 0. 先确认有 Python（后端是 Python 脚本，插件不会替你装）
python3 --version
#    需要 3.9+。没装的话二选一：
#      xcode-select --install   # 系统手段：装完自带 /usr/bin/python3，不需要 sudo
#      brew install python      # 已装 Homebrew 时最快（本机 brew info python → python@3.14）

./build.sh      # dotnet publish → ./out（需要 .NET 9 SDK）
./install.sh    # 安装到 Jellyfin 插件目录并重启 Jellyfin
```

> Linux / Windows 的安装命令不在这里重复：`apt-get install -y python3` / `dnf install -y python3` / `winget install Python.Python.3.12`（Windows 上记得勾 “Add python.exe to PATH”）写在 [PythonInstaller.cs](Jellyfin.Plugin.JellyfinDownloader/PythonInstaller.cs)。插件只做**探测**不代装：解释器装好后由 `PythonLocator` 自动找到；那个文件现在只剩这份跨平台安装指引，没有调用者，读数时按文档看。

`install.sh` 会：先停掉 Jellyfin → 复制 DLL / `meta.json` / `icon.png` / `backend/` → 保留原有的 `backend/state/`（历史快照不丢）→ `open -n /Applications/Jellyfin.app` 重新拉起。

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
| 服务端口 | `8123` | 只读展示后端实际监听的端口（只绑本机），同一行还有运行状态与「刷新状态 / 重启后端」。**被别的进程占用时自动往后试下一个**，不会换掉别人的服务 |
| 媒体目录 | 留空 | 留空 = 由插件从 Jellyfin 媒体库配置解析出库根。下载中/未完成的文件放在媒体根同盘的 `.staging`（保证归档能同盘原子移动），这一项固定，配置页里只回显、不可改 |
| 资源站 | 内置两家 | **每行一个站点地址**。地址含 `{q}` 占位符 = 该站的搜索页（云盘通道入口，只发云盘资源、没有搜索 API 的站就填这种）；其余地址按主机判定入口：内置那两家只填主机名会自动补上接口路径，别的主机则按你填的地址当接口用。默认显示内置那两家，把行全删掉 = 用内置默认 |
| 关键词组合 | 内置三条 | 每行一个关键词，与片名拼成一条查询并排在搜索阶梯最前。**一行只放一个词**：搜索站对一行里的多个词是「全部命中」语义，混进一个索引里没有的词，这一行就什么都搜不到（实测过）。默认 `高清剧集网`、`DDHDTV`、`BBHDTV` |
| TMDb API Key | 空 | 热门入口的数据源。去 TMDb 账号设置里申请后填在这里；**只存在本机插件配置，不会下发到浏览器**。留空 = 热门页只显示配置引导 |
| TMDb 语言 | `zh-CN` | 榜单标题与简介的语言 |
| 热门榜单 | 全选 | 本周热门电影 / 本周热门剧集 / 流行电影 / 流行剧集，勾选哪些就拉哪些 |
| 关注占位 | 关 | 打开后「关注」会在媒体库写占位文件，让未下载的条目先有库页；关闭 = 只记录关注，对媒体库零写入 |

> Python 解释器与暂存目录不在配置页里：解释器装好后由插件自动探测，要指定某个解释器就直接改插件配置 XML 的 `BackendPython`；暂存目录固定为媒体根下的 `.staging`。

> 资源站、关键字与 TMDb API Key 由插件写进后端的环境变量（`JMD_BT_SITES` / `JMD_L1_KEYWORDS` / `JMD_TMDB_API_KEY`），所以**保存后会自动重启后端**才生效。配置坏掉时后端会打印 `SOURCE_CONFIG_ERROR` 并明确失败，不会悄悄用默认值顶替。

**开始使用**：进入任意电影 / 剧集 / 单集详情页 → 点 **「获取资源」**。

- 剧集页会先列出季；选定季再点「重新获取」才会抓取（换季读各自的快照，不重复搜索）。
- 面板里的 **最低分阈值** 默认取插件配置的 `MinScore`（60），可随时调整。
- **严格按季**（默认开）只搜当前季并过滤无季号 / 同名噪音；**含整季包**（默认关）额外纳入整季合集的候选。

**开始使用（热门）**：先在配置页「热门」组填 TMDb API Key（不填则只能看到配置引导）→ 点右上角 **「热门」** 图标 → 挑一个标题进打分流程，或点卡片上的「关注」把它记下来；库内已有同名同年条目时卡片会标「已在库」。

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
│   ├── PythonLocator.cs                    # 探测本机 python3（PATH/Homebrew/python.org/pyenv/conda/uv）
│   ├── PythonInstaller.cs                  # 只剩跨平台安装命令这份指引；无调用者，供人照做
│   ├── MediaPaths.cs                       # 从媒体库配置解析媒体根 / 暂存区
│   ├── Controllers/JellyfinDownloaderController.cs  # 全部 HTTP 端点 + 后端代理
│   ├── Middleware/                         # 往 /web/index.html 注入脚本与样式
│   ├── Web/inject_0*.js, Web/style.css     # 面板前端（内嵌资源）
│   └── Configuration/                      # 配置项与配置页
├── backend/                                # 自带 Python 后端（自包含，零第三方依赖）
│   ├── console_server.py                   # HTTP 服务 + 面板快照（插件按这个固定路径启动它）
│   ├── build_console.py                    # 快照组装；定义 MAIN_SCRIPTS（也是脚本查找根与 sys.path 根）
│   ├── media_download_lib.py               # 数据根推导：DATA_ROOT = 本文件所在目录 / state
│   ├── candidate_score.py                  # 打分（四维权重）
│   ├── source_config.py                    # 资源站清单 + L1 关键字组合（唯一事实源）
│   ├── torrents_csv_source.py              # 第二个磁力源（torrents-csv 快照）
│   ├── probe/                              # 探针链：健康度 → metadata → 打分 → 交付
│   ├── search/                             # 取源链：diao 索引 + torrents-csv + 池合并
│   ├── pan/                                # 云盘分享：找链接 / 入池 / 转存取回
│   ├── download/                           # 提交迅雷 → 监视 → 校验 → 归档
│   └── state/                              # 运行时数据（不入库）：pool / watches / snapshots / pan

> 为什么这四个模块留在 `backend/` 根、其余进子包：`console_server.py` 由插件按
> `backend/console_server.py` 固定路径启动；`build_console.py` 所在目录同时是脚本查找根与
> `sys.path` 根；`media_download_lib.py` 所在目录决定运行时数据根 `backend/state/`。
> 三者一旦挪动就会分别打断插件启动、脚本查找与数据位置。子包里的入口脚本自带一段
> `sys.path` 引导，所以 `python3 backend/probe/probe_magnets.py` 这类直接调用仍然可用。
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
| 后端数据 | `…/JellyfinDownloader_1.0.0.0/backend/state/`（候选池、watcher、快照、云盘） |
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
看配置页「服务端口」那行的运行状态：显示「已停止」就点旁边的 **重启后端**；端口被占用时它会自己往后换。日志在 `plugins/configurations/jellyfin-downloader-backend.log`。

**没装 Python？**
先按「快速开始」第 0 步装好（3.9+），装完回配置页点「刷新状态」。插件自动探测 PATH 与常见安装位置；要指定某个解释器，改插件配置 XML 的 `BackendPython`。

**想让它出现在 Jellyfin 的插件目录里？**
两个地址都能用：
- **公开插件仓库**（推荐，插件还没装时也能用）：`https://raw.githubusercontent.com/leigangzhang/jellyfin-plugin-downloader/main/manifest.json`
- **插件自带的本地端点**（插件装好之后才有）：`http://127.0.0.1:8096/JellyfinDownloader/manifest.json`

控制台 → 插件 → 仓库 → 添加其一即可，之后可在 Jellyfin 内安装与更新。不想要就随时删掉这条仓库；卸载插件时也建议一并删除，否则每次启动都会去拉一个已失效的 manifest（`./uninstall.sh` 会提示你）。

---

## 开发说明

- **前端**：改 `Jellyfin.Plugin.JellyfinDownloader/Web/inject_0*.js`（5 个分片，装配顺序见 controller 的 `ScriptSegments`）或 `style.css`（都以 `EmbeddedResource` 内嵌进 DLL），必须重新 `./build.sh && ./install.sh` 才生效。
- **打分**：四维权重在 `backend/candidate_score.py` 的 `DEFAULT_WEIGHTS`；速度这一维只读做种者数与外部索引快照（aria2c 短窗测速已删除）。改完只需重装后端（`install.sh` 会带上 `backend/`）。
- **取源与配置**：站点清单、`kind` 适配器与 L1 关键字组合的默认值和校验都在 `backend/source_config.py`；`search_pool.py` 与 `console_server.py` 都只读这一份，不要在别处再定义一套。
- **后端边界**：`backend/` 是随插件走的自包含副本，数据根固定为 `backend/state/`（可用 `JMD_DATA_DIR` 覆盖），与作者本机的下载 skill 完全隔离，互不读写。
- **后端环境变量**：由插件注入 `JMD_MEDIA_ROOT`（媒体库根）与 `JMD_STAGING_ROOT`（暂存区），脚本读不到时才回落到默认值。
- **手工验证**：取源打分链已有离线契约测试（`tests/backend_probe/test_backend_scoring.py` 等，跑 `python3 -m unittest discover -s tests`）；下载/验证/归档路径仍靠 `./uninstall.sh --verify-only`、Jellyfin 日志、面板快照三者交叉确认。

### 常用命令

```sh
./build.sh                                        # 构建插件与内嵌的 Web 资源 -> ./out
./install.sh                                      # 构建 + 停 Jellyfin + 覆盖 + 重启
./uninstall.sh                                    # 干净卸载（不停服、不重启，带自检）
python3 tools/change-scope.py --base origin/main  # 本次改动命中哪些改动面、各欠什么证据
python3 tools/run-evidence.py --base origin/main  # 真的跑那些证据，给出三态判定
python3 tools/check-invariants.py                 # 可执行约定（含面板行为守卫 ui-p2-guard）
python3 tools/check-invariants.py --self-test     # 证明每条检查都能变红（含守卫的 --mutate 三条）
python3 tools/check-invariants.py --ui-web-dir D  # 用另一份 Web 分片跑守卫（演练回归，不动工作树）
node tools/checks/check-p2-ui.mjs --mutate        # 单独跑守卫：把三条已修好的旧行为改回去，逐条变红
python3 tools/gen-docs.py --check                 # docs/check-catalog.md 仍是当前投影
python3 tools/pair-docs.py --check                # 每对双语文档齐全且同步
python3 -m unittest discover -s tests             # 套件自己的契约测试
```

「通过」要说明它凭什么算证据：验收标准每条带编号并点名归属检查，新守卫要有人见过它红。

### 发一个新版本

1. 改 `meta.json` 的 `version`（与 `csproj` 里的 `<Version>` 保持一致）；
2. `./build.sh`；
3. 打包：`JellyfinDownloader_<版本>.zip`，**zip 根目录**放 `Jellyfin.Plugin.JellyfinDownloader.dll`、`meta.json`、`icon.png` 和 `backend/*.py`（不要带 `backend/state`、`__pycache__`、`*.log`）；
4. `gh release create v<版本> JellyfinDownloader_<版本>.zip`；
5. 更新根目录 `manifest.json`：在 `versions` 数组最前面加一条新的 `{version, changelog, targetAbi, sourceUrl, checksum(md5), timestamp}`（`checksum` 用 `md5 -q <zip>`）。

---

## 说明

个人自用项目，未附开源许可证。插件入口仅管理员可用，后端只监听 `127.0.0.1`。
