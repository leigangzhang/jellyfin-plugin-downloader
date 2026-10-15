# Decision: 下载插件里的热门入口，以及关注条目在 Jellyfin 库中占位

[English](2026-10-15-console-trending-anchor.md) | 中文

Status: implemented
Date: 2026-10-15
Class: feature

## Problem

插件只能从库里已经存在的条目进入：详情页按钮，或右上角入口的手动表单。对一部库里还没有的片子无从下手，而在「看到它」与「下载它」之间，没有任何地方记着「我想要」。三个缺口都是在这台机器上量出来的，不是推断：没有发现面（插件里没有任何东西够得着 TMDb，而库里 170 部剧集有 159 部是 0 集）；没有愿望状态（手动表单每次重打）；关注的条目在库里没有落脚点（未下载片子的库页是手工建的，形状完全一致）。`Movies/` 下 75 部电影全是同一个 8,307 字节的占位 MP4（md5 `59563f02cff1daea13f1082b80fa695c`），每个目录带 `movie.nfo`、`poster.jpg`、`fanart.jpg`；演员经 `PeopleBaseItemMap` 到位，尽管 NFO 里没有 `<actor>`。

## Decision

新增第二个右上角入口 **「热门」**：在覆盖层里列出 TMDb 的本周热门与流行电影/剧集，并能把关注的条目在库里占位。它是 `Jellyfin.Plugin.JellyfinDownloader/Web/inject_06_trending.js`，`ScriptSegments` 的第六片也是最后一片；按钮自带 class `.jdl-trending-btn` 与图标 `trending_up`，因为 `inject_01_core.js` 的 `enforceSingleEntry()` 与 `inject_02_entry.js` 的 `removeHeaderButton()` 会把 header 中带 `.material-icons.arrow_downward` 的节点按重复项收敛。选中一个标题调用现有的 `openManualPanel({title, year, kind})`，于是直接落进已经跑通的打分流程。

**数据。** `backend/trending/` 由 `trending_source.py`（基于 `urllib.request` 的 TMDb 客户端，opener 可注入）、`trending_cache.py`（按 `(language, 榜单 id)` 建键的 TTL 缓存，21600 秒，临时文件加 `os.replace` 的原子写，过期降级）与 `trending_watchlist.py`（关注清单，按 `tmdb_id` 幂等）组成。`GET /api/trending` 与 `POST /api/trending/watch` 接进 `backend/console_server/http_api.py`；数据根是 `media_download_lib.DATA_ROOT`（`JMD_DATA_DIR` 可整体搬走），这个包不用 `__file__` 拼任何路径。`GET /api/trending` 的响应是

```
{"lists":[{"id","label","items":[{"tmdb_id","title","original_title","year",
   "kind","poster_path","vote_average"}]}],
 "stale":<bool>, "guidance":null, "watched":[<tmdb_id>]}
```

四个榜单 id 是 `trending_movie_week`、`trending_tv_week`、`popular_movie`、`popular_tv`；`JMD_TMDB_API_KEY` 缺失时返回 `lists: []` 与一段中文 `guidance`，此时这条路由**不触网**。`POST /api/trending/watch` 回 `{"ok","exists","written"}`。

**库内状态。** `POST /JellyfinDownloader/trending/library-status` 按标题批量回答「库里有没有」（归一化片名加 `ProductionYear`，保持请求顺序）；`POST /JellyfinDownloader/trending/anchor` 写占位。两者都放插件侧而不是后端：库路径的唯一事实源是 `MediaPaths.MediaRoot` 与 `ILibraryManager`，而 `backend/` 不许携带机器相关路径。两条端点都接受平台自己的 kind 词表（`movie`/`film`、`series`/`episode`/`tv`/`show`/`variety`、`record`/`records`）并归一成库类别（`movie` → `Movies`、`series`/`episode`/`variety` → `TV Shows`、`record` → `Records`）；请求缺 4 位年份时 `anchor` 直接拒绝（`400 {"error":"year is required"}`），`library-status` 也绝不据此宣称 `exists: true`——因为无年份的落点永远不可能等于真条目目录，两者会变成两条独立条目。

**占位。** `Web/placeholder.mp4`（8,307 字节，md5 `59563f02cff1daea13f1082b80fa695c`——就是库里已经在用的那个文件）作为 `EmbeddedResource` 随插件走，清洗片名并校验落在 `MediaPaths.MediaRoot` 之内后，写为

```
Movies/<title> (<year>)/<title> (<year>) - zz placeholder.mp4
TV Shows/<title> (<year>)/Season 01/<title> S01E01 - zz placeholder.mp4
```

写入走同目录临时文件加 `File.Move`。目标已存在则只报告、绝不重写。这个形状由两条约束逼出来：`atomic_same_volume_move` 在目标存在时抛 `FileExistsError`，而 `normalized_destination` 把真文件放在恰好 `<title> (<year>).<ext>`，所以不带标签的占位会在下载回来是 MP4 时打断归档；而 Jellyfin 只把「文件名在版本标签之前逐字符以文件夹名开头」的文件当作同一部片的版本，且播放字母序列表里的第一个——标签既要与真名分开，又要排在最后。

**配置。** `PluginConfiguration` 新增 `TmdbApiKey`（空）、`TmdbLanguage`（`zh-CN`）、`TrendingLists`（四个榜单 id）与 `AnchorEnabled`（`false`）。`BackendManager.Start` 只在 key 非空时注入 `JMD_TMDB_API_KEY`，而 `GET /JellyfinDownloader/config` 只回 `tmdbApiKeyConfigured`，不回 key 本身。配置页新增一组「热门」；`AnchorEnabled` 关闭即对媒体库零写入。`GET /JellyfinDownloader/config` 同时下发 `guidance`，作为覆盖层无 key 文案的唯一来源；`GET /api/trending` 自己的 `guidance` 字段保留给直连 API 的消费者。

**不在本次范围。** 不自动下载、不批量提交、不加首页行、不劫持路由、不接 Seerr 与 *arr，也不删除占位。清理若将来要做，归 watcher 阶段。

## Alternatives considered

**把发现交给 Seerr 与现成插件。** `SeerrFin`（TMDb 热门轮播 + 请求弹窗）与 `Home Screen Sections`（Jellyseerr 的 Trending 与 Discover 行）都能解决发现问题，但两者目标 ABI 是 12.0 而本机跑 10.11.11，且都需要本机没部署的 Seerr 实例；它们的下游还是 Sonarr/Radarr，而这套流程不用它们。

**从已配置的资源站读榜单。** 零新凭据、结果直接可下，但站点的「热门」只是它自己的最新发布，没有海报、演员和评分，站点一改版就断。留作可能的第二个 provider。

**用豆瓣当数据源。** 没有官方 API，抓取不稳且会被封。否决。

**让浏览器直接打 TMDb。** 会把 key 暴露给每个客户端，并破坏现有的同源代理边界。否决；key 只留在插件进程里，浏览器最多看到 `tmdbApiKeyConfigured`。

**做真正的路由 `/web/#/jdl/trending`。** 要么劫持 Jellyfin 路由，要么依赖 Plugin Pages；覆盖层更小，也让插件保持自包含。

**归档时让真文件覆盖占位。** `atomic_same_volume_move` 拒绝覆盖已存在的目标，而且用户明确要求归档不动占位，所以改由命名避开冲突。

**真文件到位后删掉占位。** 那会彻底消掉第二个版本，但用户已否决；若播放真的默认落到占位，退路是交给 watcher 阶段的清理。

**只把关注记进 watchlist，不碰媒体库。** 最省事，但丢掉了全部意义：关注的条目在下载落地之前没有库页、没有海报、没有演员。

## Consequences

- 插件现在能从库里还没有的片子进入：右上角入口列出 TMDb 的热门与流行榜单，选中的标题连同年份与类型进现有打分流程。关注一个条目会写进 `backend/trending`（watchlist），并在 `AnchorEnabled` 打开时在媒体库里留下一个占位文件。
- 没有引入任何第三方依赖：TMDb 客户端只用 `urllib.request`，占位模板是内嵌资源，覆盖层不加载任何外部脚本。
- 按钮自带的 class 与图标是承重的：一旦与既有入口共用 `.jdl-header-btn`/`arrow_downward`，两个 header 入口会互删。
- 本次没能验证真实 TMDb 流量：这台机器出网到 `api.themoviedb.org` 超时，所以「拉真榜单」这条只有注入假 opener 的证据，真要跑通还需要一台能出网的机器加配置页里的 key。无 key 的引导路径、TTL、过期降级与 watchlist 都在不联网的情况下跑过。
- `anchor` 只写占位文件并建目录；关注条目显示的海报、演员与评分来自 Jellyfin 自己的元数据 provider，不是本次改动给的。provider 关掉的库只会显示一个由文件名派生的名字、没有封面。
- `Jellyfin.Plugin.JellyfinDownloader/Controllers/JellyfinDownloaderController.cs` 在这次改动里从 750 行长到 1,266 行（30 KB → 50.8 KB），越过了读文件工具的 50 KB 上限；`source-budget` 覆盖 `backend/*.py`、`tests/*.py` 与 `Web/*.js`，不含 `.cs`，所以没有闸门变红。拆控制器留作后续改动，没有塞进这一次。
- TMDb 记录里没有年份的条目可以被关注（进 watchlist），但永远不会被占位：缺年份时覆盖层跳过 anchor 调用，而不是发一条端点必然会拒的请求，卡片因此显示「仅记录」（`listed`），不会把拒绝当成错误弹出来。
- 唯一会污染库的失败模式是占位名与归档名分叉：两者会变成两条独立条目。A7 把它们钉在一起；清洗规则在插件与 `finalize.py` 之间是故意重复的，所以 A7 是让这份副本保持诚实的唯一东西。
- 如果 Jellyfin 的版本排序规则变了、占位真的成了默认播放对象，或占位将来需要被删除或挪进归档路径，就回来重新审视这个决定。

## Testing

- [A1] 未配置 key 时覆盖层渲染 `GET /JellyfinDownloader/config` 下发的 `guidance` 且控制台干净：后端半边由 `tests/backend_trending/test_backend_trending_routes.py` 钉住 `http_api.py` 的路由与 `backend/trending/trending_source.py` 的引导分支；渲染半边是交付报告里记录的真机检查。 归属 `source`, `tests`。
- [A2] `tests/backend_trending/test_backend_trending_source.py`——假 opener 返回 ≥2 个榜单、每榜 ≥10 条，字段形状同上。 归属 `tests`。
- [A3] `tests/backend_trending/test_backend_trending_cache.py`——TTL 内第二次调用不打 TMDb，由注入时钟与 opener 调用计数证明。 归属 `tests`。
- [A4] `tests/backend_trending/test_backend_trending_stale.py`——TMDb 不可达时返回上次缓存并置 `stale: true`。 归属 `tests`。
- [A5] 交付报告里的真机验收（交付阶段 4）：关注一部电影后磁盘上留下 `Movies/<title> (<year>)/<title> (<year>) - zz placeholder.mp4`；占位路径同时在单元层由 `tests/backend_retired/test_backend_retired_trending_anchor_naming.py` 钉住。 归属 `source`。
- [A6] 同一次真机验收：关注一部剧集后留下 `Season 01/<title> S01E01 - zz placeholder.mp4`，且库里显示该剧一集。占位分集能否建出剧集在交付前已在本机确认，不是假设。 归属 `source`。
- [A7] `tests/backend_retired/test_backend_retired_trending_anchor_naming.py` 读控制器的命名常量，断言同一片名与年份下占位路径永不等于 `media_download_lib.normalized_destination` 的落点（电影与剧集两种都查）。 归属 `tests`。
- [A8] `tests/backend_trending/test_backend_trending_check_exists.py` 以子进程跑 `backend/download/check_exists.py`，对同时放着占位与真文件的临时媒体根要求输出 `ALREADY_EXISTS`；只有占位的目录仍报 `NEEDS_DOWNLOAD`。 归属 `tests`。
- [A9] `tests/backend_trending/test_backend_trending_watchlist.py` 钉住重复关注报已存在且不重写文件；占位侧（`AnchorEnabled` 关闭零写入、已存在的占位绝不重写）由 `tests/backend_retired/test_backend_retired_trending_anchor_naming.py` 与真机验收钉住。 归属 `tests`。
- [A10] `tests/backend_trending/test_backend_trending_no_machine_paths.py` 拒绝 `backend/trending/` 下由 `__file__` 推导的数据路径、机器相关绝对路径与第三方 import；仓库级的另一半是 `tools/check-invariants.py` 里的 `no-secrets` 与 `source-budget` 两条检查。 归属 `source-budget`, `no-secrets`。
- [A11] 交付报告里的真机验收：`./build.sh` 与 `./install.sh` 成功，配置页出现「热门」分组，`/JellyfinDownloader/script` 带第六片，覆盖层能渲染——用浏览器工具对着运行中的 Jellyfin 做，榜单响应在浏览器侧替换，因为这台机器出不了 TMDb。 归属 `source`。
- [A12] 带上这一对记录后 `python3 tools/gen-docs.py --check` 与 `python3 tools/pair-docs.py --check` 全绿。 归属 `i18n`, `docs`。
