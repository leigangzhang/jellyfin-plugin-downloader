# Proposal: 下载插件里的热门入口，以及关注条目在 Jellyfin 库中占位

[English](2026-10-15-console-trending-anchor.md) | 中文

Status: proposed
Date: 2026-10-15
Class: feature

## 问题

插件目前只能从库里已经存在的条目进入：详情页按钮，或右上角入口的手动表单。对一部库里还没有的片子无从下手，而在「看到它」与「下载它」之间，没有任何地方记着「我想要」。

三个缺口都是在这台机器上量出来的，不是推断：

1. **没有发现面。** 插件里没有任何东西够得着 TMDb，库本身也回答不了「现在有什么值得看」——它那 170 部剧集里有 159 部是 0 集。
2. **没有愿望状态。** 想要的片子没有任何地方记着，手动表单每次重打。
3. **关注的条目在库里没有落脚点。** 未下载片子的库页现在是手工建的，而形状完全一致。

第三个缺口也正是这份提案很小的原因：机制已经存在，只是没有自动化。`Movies/` 下 75 部电影全是同一个 8,307 字节的占位 MP4（md5 `59563f02cff1daea13f1082b80fa695c`），每个目录还带 `movie.nfo`、`poster.jpg`、`fanart.jpg`。演员也一样到位：`PeopleBaseItemMap` 有 4,753 行，抽查那部电影能解出五个带角色的演员，尽管 NFO 里没有 `<actor>`。

## 提案

新增第二个右上角入口 **「热门」**，列出 TMDb 的本周热门与流行电影/剧集，并能把关注的条目在库里占位。

**数据。** 新增 `backend/trending/` 子包：`trending_source.py`（基于 `urllib.request` 的 TMDb 客户端，形状照 `backend/search/search_pool_api.py`）、`trending_cache.py`（TTL 缓存、临时文件加 `os.replace` 的原子写、过期降级）、`trending_watchlist.py`。两条路由接进 `backend/console_server/http_api.py`：`GET /api/trending` 与 `POST /api/trending/watch`。数据根取自 `media_download_lib` 的 `DATA_ROOT`；这个包不许用 `__file__` 拼路径。

**前端面。** `Web/inject_06_trending.js` 以覆盖层渲染海报墙，并复用现有的 `openManualPanel({title, year, kind})`，于是选中的热门条目直接落进已经跑通的打分流程。它的 header 按钮必须用独立 class `.jdl-trending-btn` 与独立图标 `trending_up`：`inject_01_core.js` 里的 `enforceSingleEntry()` 与 `removeHeaderButton()` 会把 header 中带 `.material-icons.arrow_downward` 的节点按重复项收敛，否则两个入口会互删。登记脚本分片的三处必须同一次改动：`Web/` 下的文件、csproj 的 `EmbeddedResource`、`ScriptSegments`。

**库内状态。** `POST /JellyfinDownloader/trending/library-status` 按标题批量回答「库里有没有」，形状照现有 `seasons` 端点走 `ILibraryManager`。`POST /JellyfinDownloader/trending/anchor` 写占位。两者都放插件侧而不是后端：库路径的唯一事实源是 `MediaPaths` 与 `ILibraryManager.GetVirtualFolders()`，而 `backend/` 被禁止携带机器相关路径。

**占位命名。** 模板随插件带 `Web/placeholder.mp4`（8,307 字节，就是库里已经在用的那个文件），写为：

```
Movies/<title> (<year>)/<title> (<year>) - zz placeholder.mp4
TV Shows/<title> (<year>)/Season 01/<title> S01E01 - zz placeholder.mp4
```

这个形状由两条已核实的约束逼出来。其一，`atomic_same_volume_move` 在目标存在时抛 `FileExistsError`，而 `normalized_destination` 把真文件放在恰好 `<title> (<year>).<ext>`；不带标签的占位会在下载回来是 MP4 时直接打断归档。其二，Jellyfin 只把「文件名在版本标签之前逐字符以文件夹名开头」的文件当作同一部片的版本，而播放的是字母序列表里的第一个，所以标签既要与真名分开，又要排在最后。

**配置。** `PluginConfiguration` 新增 `TmdbApiKey`、`TmdbLanguage`（默认 `zh-CN`）、`TrendingLists` 与 `AnchorEnabled`（默认 false）；配置页新增一组。key 以 `JMD_TMDB_API_KEY` 传给后端。`AnchorEnabled` 关闭即对媒体库零写入。

**不在本次范围。** 不自动下载、不批量提交、不加首页行、不劫持路由、不接 Seerr 与 *arr，也不删除占位。清理若将来要做，归 watcher 阶段。

## 曾考虑的替代方案

**把发现交给 Seerr 与现成插件。** `SeerrFin`（TMDb 热门轮播 + 请求弹窗）与 `Home Screen Sections`（Jellyseerr 的 Trending 与 Discover 行）都能解决发现问题，但两者目标 ABI 是 12.0 而本机跑 10.11.11，且都需要本机没部署的 Seerr 实例；它们的下游还是 Sonarr/Radarr，而这套流程不用它们。

**从已配置的资源站读榜单。** 零新凭据、结果直接可下，但站点的「热门」只是它自己的最新发布，没有海报、演员和评分，站点一改版就断。留作可能的第二个 provider。

**用豆瓣当数据源。** 没有官方 API，抓取不稳且会被封。否决。

**让浏览器直接打 TMDb。** 会把 key 暴露给每个客户端，并破坏现有的同源代理边界。否决。

**做真正的路由 `/web/#/jdl/trending`。** 要么劫持 Jellyfin 路由，要么依赖 Plugin Pages；覆盖层更小，也让插件保持自包含。

**归档时让真文件覆盖占位。** `atomic_same_volume_move` 拒绝覆盖已存在的目标，而且用户明确要求归档不动占位，所以改由命名避开冲突。

**真文件到位后删掉占位。** 那会彻底消掉第二个版本，但用户已否决；若播放真的默认落到占位，退路是交给 watcher 阶段的清理。

**只把关注记进 watchlist，不碰媒体库。** 最省事，但丢掉了全部意义：关注的条目在下载落地之前没有库页、没有海报、没有演员。

## 验收标准

- [A1] 未配置 key 时热门页渲染配置引导且控制台干净 —— 归属 `source`。
- [A2] 用假 opener 时 `GET /api/trending` 返回至少两个榜单、每榜至少十条 —— 归属 `tests`。
- [A3] TTL 内二次调用不打 TMDb，用注入时钟证明 —— 归属 `tests`。
- [A4] TMDb 不可达时返回上次缓存并带上过期标记 —— 归属 `tests`。
- [A5] 关注一部电影后磁盘上留下 `Movies/<title> (<year>)/<title> (<year>) - zz placeholder.mp4` —— 归属 `source`。
- [A6] 关注一部剧集后留下 `Season 01/<title> S01E01 - zz placeholder.mp4`，且库里显示该剧一集 —— 归属 `source`。
- [A7] 同一片名与年份下，占位路径永不等于 `normalized_destination` 的落点 —— 归属 `tests`。
- [A8] 目录里同时有占位与真文件时 `check_exists` 仍给出「已存在」判定 —— 归属 `tests`。
- [A9] 重复关注报已存在且不重写文件；占位开关关闭时零写入 —— 归属 `tests`。
- [A10] 无第三方依赖、仓库内无密钥、`backend/` 下无机器相关路径 —— 归属 `source-budget` 与 `no-secrets`。
- [A11] 插件能构建、能加载，热门页在真实服务器上渲染 —— 归属 `source`。
- [A12] 每份新增文档都成对，检查目录仍与其生成器一致 —— 归属 `i18n` 与 `docs`。

## 风险

唯一会污染库的失败模式是占位名与归档名分叉：两者会变成两条独立条目。A7 把它们钉在一起；清洗规则在插件与 `finalize.py` 之间是故意重复的，所以 A7 是让这份副本保持诚实的唯一东西。

「只有占位一集的目录会不会真的建出剧集条目」未核实：库里那 159 个 0 集条目更像是残留而非证据，而本机服务端的 `SeriesResolver` 只在混合内容库里接受单独的 `tvshow.nfo`。第一个交付阶段就是判定它的实验；结果为否时，剧集降级为只记 watchlist，电影照旧。

「默认版本会不会落到真文件」同样未核实：Jellyfin 播放字母序版本列表的第一个，而无标签版本的排序位置没有观察过。结果为否时启用归 watcher 所有的清理，那也是唯一会删占位的路径。

如果 Jellyfin 的版本排序规则变了，或占位真的成了默认播放对象，就回来重新审视这个决定。

## 交付阶段

1. 探针 —— 一个一次性目录 `__jdl_probe__ (2026)` 加一个电影目录，判定占位分集能否建出剧集、以及默认播哪个版本；两处事后都删，首项结果为否则把剧集降级为只记 watchlist。
2. 后端 —— `backend/trending/`、两条路由，以及 `tests/backend_trending/` 下的离线套件。
3. 插件 —— 配置项、`trending/anchor`、`trending/library-status`、占位资源与配置页分组。
4. 客户端 —— 热门分片、样式、入口去重改造。
5. 记录 —— implemented 记录、README 与 AGENTS 更新，以及成对文档。
