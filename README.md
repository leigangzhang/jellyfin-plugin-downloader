# Jellyfin Downloader 插件

在 Jellyfin 电影 / 剧集 / 单集详情页注入「获取资源」按钮（仅管理员）：触发本机搜索与打分，列出 ≥60 分候选，支持复制磁力 / 提交迅雷 / 后台监控（云盘候选可打开分享页）。风格与 Jellyfin 原生一致。

## 结构

```
Jellyfin.Plugin.JellyfinDownloader/
├── Plugin.cs                      # IPlugin + IHasWebPages（配置页）
├── PluginServiceRegistrator.cs    # 注册注入中间件
├── BackendManager.cs              # 后端进程启停与状态
├── Middleware/                    # 改写 /web/index.html，追加 script/style
├── Controllers/                   # /JellyfinDownloader/{script,style,config,api/**}
├── Configuration/                 # 插件配置 + 配置页 HTML
└── Web/                           # inject.js / style.css（内嵌资源）
backend/                           # 自带独立后端（16 个 py，已与 skill 剥离）
meta.json                          # targetAbi 10.11.11.0
```

`backend/` 是插件的独立后端：含 `console_server.py`、`build_console.py` 及其依赖的
14 个脚本（candidate_score / media_download_lib / probe_magnets / search_pool /
pan_* / submit_xunlei / watch_download / verify_media / cleanup_download …）。
入口与依赖同目录，`MAIN_SCRIPTS` 指向自身，**不再引用 `~/.agents/skills`**；

## 数据（插件自持，与 skill 隔离）

插件自带后端**自己维护全部数据**，全部落在 `backend/state/` 下，随插件目录走：

```
backend/state/
├── pool/<片名>.json              # 候选池（按整剧累积，含 metadata/健康度缓存）
├── watches/<ih>.json + .log      # watcher 运行态与日志
├── pan/<片名>.json + .log        # 云盘转存状态机
├── marks/<片名>.json             # 面板人工标记
└── snapshots/<片名>[.sNN].{probe,context,pan}.json   # 分季抓取快照
```

- 数据根 = `<本文件同级>/state`，可用 **`JMD_DATA_DIR`** 整体覆盖；
  `JMD_CONSOLE_STATE_DIR` 仍可只覆盖 marks/snapshots 这一部分。
- `install.sh` 重装时会保留整个 `backend/state/`（含迁移过来的历史 pool/watches）。
- 主 skill 的 `~/Library/Application Support/JellyfinDownloader/` 插件**不再读写**，
  两边完全隔离；插件目录删掉不会影响 skill，反过来的历史数据也已复制进插件。
- **数据格式以插件这份脚本为准**：若与 skill 侧脚本写出的格式出现冲突，
  按插件写出的格式走（skill 侧只作历史基线，不再作为对齐目标）。

## 构建与安装

```bash
./build.sh     # 需要 .NET 9 SDK（brew install dotnet@9）
./install.sh   # 构建 + 拷贝到 Jellyfin 插件目录 + 重启 Jellyfin
```

## 后端启停（不走 launchd）

后端随插件启用自动启动，不需要单独点「启动」：

- 插件加载时（Jellyfin 启动 / 插件启用）通过 `IHostedService` 自动拉起 **插件自带的 `backend/console_server.py`**（默认 `127.0.0.1:8123`，脚本字段留空即用自带路径）；后端已在运行则直接识别、不重复拉起。
- Jellyfin 退出时，插件会自动停掉自己拉起的后端（`StopAsync` → `StopQuietly`）。
- 配置页（控制台 → 插件 → Jellyfin Downloader）只保留 **端口 / 后端服务状态（运行中/已停止 · pid · 管理方式）+「刷新状态」「重启后端」/ 暂存目录 / 媒体库落点（只读）**。最低分数、后端地址、Python 解释器、启动脚本都不再暴露，走内置默认（`127.0.0.1:8123`、自带脚本、阈值 60）。
- 后端日志：`~/Library/Application Support/jellyfin/plugins/configurations/jellyfin-downloader-backend.log`。

## 媒体库落点与暂存目录

- **媒体目录**（配置页「媒体目录」，`MediaRoot`）：留空时由插件从 Jellyfin 的库配置解析——取各库路径的公共父目录作为 `MEDIA_ROOT`，下面按 Movies / TV Shows / Shows / Records 分类；也解析不到时退回占位默认 `~/Media`。
- **暂存目录**（配置页「暂存目录」，`StagingRoot`）：留空时按「同盘暂存」推导为媒体目录的兄弟目录 `.staging`（例如媒体目录是 `/path/to/media`，暂存就是 `/path/to/.staging`），保证归档能用同盘 `mv` 原子完成；媒体目录层级太浅时退回 `~/Downloads/.staging`。
- 源码里不含任何机器相关路径：Python 解释器默认用 PATH 上的 `python3`，两个目录默认都从上表推导，可在配置页覆盖（也可以直接改 `Jellyfin.Plugin.JellyfinDownloader.xml`）。
- 插件启动后端时把这两个值作为 `JMD_MEDIA_ROOT` / `JMD_STAGING_ROOT` 环境变量传给后端，`media_download_lib` 读到就采用，读不到才用自己内置的硬编码兜底。

## 进程管理（防无限增长 / 失控）

后端是长驻单实例，靠三样东西保证不会越积越多：

- **pidfile 单实例**：后端启动时把 pid 写进 `backend/state/console_server.pid`，退出/收到信号时删除。重复启动会检测到 pidfile 里还活着的进程并直接拒绝（`exit 2`）；`install.sh` 里那次 Jellyfin 重启遗留的旧后端就是靠这个 + 端口检查挡掉的。
- **进程组回收**：后端每次跑 `search_pool` / `probe_magnets` / `pan_*` 都用 `start_new_session=True` 放进独立进程组，超时或退出时按组整体 `SIGTERM→SIGKILL`，连带把 `probe_magnets` 再 spawn 出来的 `aria2c` 一起收掉。以前超时只杀直接子进程，`aria2c` 会变成孤儿一直挂着。
- **优雅停止**：插件 `Stop()`/`StopQuietly()` 先发 `SIGTERM`，后端收到后按组清掉所有子进程、删 pidfile 再退出；5 秒内没退才 `SIGKILL` 兜底。直接 `SIGKILL` 会跳过收尾、留下孤儿。

验证过的三条：重复启动被拒（`拒绝重复启动`）；`SIGTERM` 后进程退出且 pidfile 删除；杀掉子进程的进程组时，其孙进程（`aria2c` 等价物）一并消失、无孤儿。

## 右上角入口：搜库里没有的资源

顶部导航栏「搜索 / 个人资料」图标左边还有一个 **获取资源图标**（`arrow_downward`，单个向下的箭头），点开是**手动搜索面板**：片名 / 别名·原名 / 年份 / 类型（剧集·综艺 或 电影）/ 季 / 集，填完点「搜索」。

- **库里没有也能搜**：不依赖详情页、不依赖 `check_exists` 命中；后端照样跑 查重 → 搜索 → 打分 → 测速，候选动作（复制磁力 / 提交迅雷 / 后台监控 / 打开云盘）与详情页面板完全一致。提交时 `final_dir` 由后端按 `片名 (年份)` 推导到媒体库里，扫库即可见。
- **不对库外条目做任何隐式写入**：只写 `pool/<片名>.json` 与 `state/snapshots/<片名>[.sNN].*`（和详情页同一套命名空间，重复搜同一部会命中同一份快照）。
- **改了条件不会自动开抓**：搜索一次要几分钟，所以表单改动只在搜索栏下方提示「搜索条件已修改：[按新条件搜索]」；回车键也能直接搜。
- 在详情页点这个入口会自动带入当前条目的片名 / 原名 / 年份 / 类型 / 季集（单集页带 S01E01，季页带季号），改一改就能搜别的。
- 面板动作统一打到 `panel.item` 上（`panelTarget()`），避免手动面板打开时详情页的 `mount()` 把 `state.item` 换掉导致搜错条目。

入口节点是**克隆原生的 header 图标按钮**（`is="paper-icon-button-light"` + `headerButton headerButtonRight`），只改图标和 aria-label，所以尺寸/圆角/hover 与搜索、个人资料图标一致；播放页沿用 Jellyfin 自己那条 `.headerButton:not(...)` 规则一起隐藏。React 重渲染会冲掉该节点，`mount()` 每次都会检查并补挂。

一屏只允许一个下载图标：

- 详情页已经有「…」旁边那个入口，右上角就**不再重复挂**（同一屏两个一模一样的下载图标很怪）。判据是 URL 里的条目 id（`currentItemId()`），不是「inline 按钮挂好没有」——后者要等异步挂载，中间那一小段仍会出现两个。详情页面板里**不放**「搜别的片名」（手动搜索只从右上角入口进，即非详情页）。
- 两个入口都用**稳定 class** 认（`jdl-inline-btn` / `jdl-header-btn`）：React 重建操作行时把 `data-*` 属性弄丢也还认得出来，`removeButton()` 按 class 一起清，不留孤儿副本。右上角那个再按「header 里带入口箭头」兜底，始终只保留紧跟搜索图标之前的 1 个。
- 有 1.5s 的兜底巡检 `enforceSingleEntry()`：观察者只在 DOM 变化时触发，这一层按时间再收一次尾，发现多余节点就删掉并在 Console 打 `[JDL] collapsed N duplicate entry node(s)`。
- `start()` 会打一行版本横幅 `[JDL] build <版本> · 入口：…`。**脚本只在整页加载时执行一次**，SPA 内部点来点去不会重新注入 —— 看不到这行横幅就说明页面跑的还是旧脚本，需要 Cmd+Shift+R 或关掉标签重开。

（实测：内置浏览器用真实登录会话跑 2026-09-25c，首页 → 右上角 1 个；电影/剧集详情页 → 只有「…」旁 1 个，页面上 download 字形总数 = 1；Jellyfin 自己的 `btnDownload` 是 `get_app` 且只对 Book 显示，本机是隐藏的。）

如果你更想在详情页也保留右上角入口（详情页就会同时看到「…」旁边和右上角两个），把 `mountHeaderButton()` 开头 `if (currentItemId()) { removeHeaderButton(); return; }` 那段去掉即可。

## 选季抓取与同名噪音过滤

多季剧集的详情页点「获取资源」**只拉季列表 + 显示历史**，不自动开搜；选好季再点「重新获取」。面板顶部两个开关（只在选了具体某一季时出现）：

| 开关 | 默认 | 作用 |
|---|---|---|
| 严格按季 | 开 | 搜索只用「第 N 季 / 第 N 季（中文数字）/ SNN / 英文原名 + SNN」，跳过基础片名、年份、平台与内置的 `全集/Complete/全` 档位 —— 否则那些档位会把整部剧的其它季一起带回来 |
| 含整季包 | 关 | 选季时默认隐藏「没标季号」的候选（可能真是整剧包，也可能属于别的季）；打开即恢复显示 |

面板还会用 `title_match` 把**同名不同作品**折叠掉（《空王冠》《罪恶王冠》《9-nine-支配者的王冠》不会混进《王冠》），并在搜索栏下方用一行「已隐藏：同名噪音 N 条 · 未标季号 M 条」+ 一键「显示同名候选 / 含整季包」暴露出来 —— 折叠必须看得见，否则用户只会觉得资源少了。判定只在**能确定**时下结论：中文片名只跟含汉字的候选比，条目没有 `OriginalTitle` 时英文名资源不会被误杀（历史快照里 56 条会误伤 12 条，已修）。

季级结果各存一份（`王冠.s01.probe.json`），第 1 季与第 2 季互不覆盖；候选池 `pool/<片名>.json` 仍然共用累积。

## 回滚

```bash
rm -rf "$HOME/Library/Application Support/jellyfin/plugins/JellyfinDownloader_1.0.0.0"
kill -TERM <jellyfin-pid> && open -n /Applications/Jellyfin.app
```

（若后端仍在运行，先在插件配置页关掉开关，或 `kill $(lsof -ti :8123)`。）
