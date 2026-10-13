# Decision: 排序流程 —— 可配置的资源站清单、发布者关键字优先，删掉测速探针

[English](2026-10-13-ranking-flow-simplification.md) | 中文

Status: implemented
Date: 2026-10-13
Class: architecture

## 问题

候选池里有三件事是写死在代码里的，而不是配置；另有一个阶段付出的代价远大于它换来
的结果。

资源站是常量（`search_pool.DEFAULT_BASE_URL`、`DEFAULT_EXTRA_SOURCES`），发布者关键字
也是（`PUBLISHER_QUERY_TOKEN = "DDHDTV"`），并且受「中英不混搭」规则限制、只在片名含
ASCII 时生效。想换一组站、或换一个发布者署名，都得改源码。通道本身也是一条固定的
降级阶梯——结构化 API、再浏览器找到的磁力文件、再本地 Pansou 实例——而云盘那一半除了
这个本地实例没有别的入口。

测速探针还接在流程里：`speed_probe.py` 每测一条候选要 17.6–24.1s，而同一个 info hash
的重复测量散布 1.16–8.50×，所以它为排序付的钱换不回排序（前一份决策记录实测跨 hash
`ρ = −0.214`）。控制台已经不传它了，但模块、参数、`measure_speeds` 阶段、以及唯一调用
它的 `/api/verify` 端点都还在——而那个端点没有任何界面在用。

## 决策

配置收敛到一个家，搜索改成「遍历配置的站」，发布者关键字提到最前，退役的路径一律删除
而不是禁用。

1. **`backend/source_config.py` 拥有这两份清单。** `JMD_BT_SITES` 与 `JMD_L1_KEYWORDS`
   （都是数组；站点的字段是 `{name, kind, base_url, web_search_url}`），后者的每一项是一条
   追加在片名后的查询后缀。**一个站就是一个地址**（配置页一行一个，用户填的地址写进
   `base_url`）：地址里带 `{q}` 就是该站的搜索页，也就是云盘通道的入口——只发云盘资源、没有
   搜索 API 的站正是云盘通道的常见入口，它的 kind 会被归一化成空串，取源侧不会把它当成磁力站。
   其余地址按主机判定：内置那两家只填主机名时补上默认接口路径；未知主机就按用户填的地址当接口
   用，除非调用方**显式声明**了 `kind`（旧 XML、脚本与自建站靠这两个显式字段继续可用）。
   一个站至少要有一个入口。空 = 用内置默认值：当前在用的两个站，加上实测有效的两条发布者署名。
   配置坏掉时报 `SOURCE_CONFIG_ERROR` 并以退出码 2 结束，**绝不**悄悄换成默认值。
2. **每个配置的站都会被问到，预算按站切分。** 单个关键词的墙钟在 API 类站点之间均分，
   非 API 站在自己的预留切片里同样均分。所以最坏墙钟始终是
   `--max-queries × --page-budget-secs`，与配了几个站无关——控制台那条
   `12 × 36s ≤ 450s` 的推算因此仍然成立。站点上限 8 个，关键字组合上限 8 条。
3. **配置的关键字就是 `anchor` 档，且排在最前。** 每条关键字生成一条 `片名 + 关键字`，
   排在别名/年份/裸片名之前，并且在 `--strict-match` 下同样运行。默认关键字取实测有效的
   两条：

   | `m.diao.im` 上的查询（2026-10-13 实测） | 命中 |
   |---|---|
   | `漫长的季节 DDHDTV` | 6/6 全为该发布者 |
   | `漫长的季节 高清剧集网` | 10/10，覆盖 DDHDTV 与 BPHDTV |
   | `漫长的季节 BTHDTV` | 10/10 |
   | `漫长的季节 高清剧集发布网` | 0 |
   | `漫长的季节 BBHDTV` | 0 |
   | `周三俱乐部 第二季 DDHDTV` | 0 |

   后三行正是「默认值不取读起来像站点名的短语」和「锚点档不与季词组合」的依据。
4. **云盘通道 = 每个带 `{q}` 的站点地址。**（`.../search?q={q}`）会走已有的
   `pan_search.py --page` 路径，控制台按站打印。一个站都没填
   时打印 `云盘通道没有入口`，而不是返回一个看不见的空。磁力搜索时若站点清单里一个
   `base_url` 都没有，会打印 `NO_MAGNET_STATION` 并以退出码 4 结束，而不是报一个空池子。
5. **删除而不是禁用：** `backend/speed_probe.py`、`probe_magnets.measure_speeds` 与
   `--speed-probe*` 参数、`console_server` 的 `/api/verify` 及其导入、
   `probe_magnets --allow-pansou` / `--allow-pansou-fallback`、`pan_search --pansou`、
   `probe_magnets --magnet` / `--info-hash`、`build_console.rescore_entry`，以及
   `search_pool --base-url` / `--extra-source` / `--extra-source-base-url`。
   `candidate_score._speed_score` 只剩四档——`seeds`、`seeds-external`、`unknown`、
   `dead-swarm`——不再读 `measured_speed_mbps`。

## 曾考虑的替代方案

**保留本地 Pansou 实例作为云盘通道。** 否决：它要求本机跑着一个插件装不了的服务
（`127.0.0.1:64056`），而且使用者要的是一个云盘入口，不是两个。

**每个站各自带一份关键字清单。** 否决：关键字是种子名里的发布者署名，不是站点的属性；
使用者也选择了「两组独立的全局列表」。

**把测速探针留成手动诊断**（前一份决策记录的选择）。否决：面板里没有任何入口调它，唯一
调它的那个端点给出的数排不动候选，而一个没人会传的参数就是一条没人测的路径。

**给内置的两个站各配一个默认 `web_search_url`。** 否决：`torrents-csv.com` 对页面抓取
返回反爬页，`m.diao.im` 是磁力索引；造一个默认值只会让云盘通道看起来配好了、实际什么都
返回不了。一个明说「没有入口」的通道比一个假装存在的入口好。

**把锚点档写成 `片名 + 季词 + 关键字` 以保住严格按季。** 否决：实测 0 命中——索引把这些
词做 AND，而季词并不是该索引在种子名里匹配的形式。

## 后果

默认一轮现在是两个站、每个片名两条锚点查询、没有测速；云盘通道在某个站填了网页搜索地址
之前一直是惰性的。锚点档不带季标记，所以严格按季的搜索**本来会**看到同一部剧的其它季；
现在由一道显式季标记收口挡住（`candidate_seasons` / `strict_season_filter`），它认种子名里
真实在用的三种写法——`SxxEyy`、裸 `Sxx`、`第N季`/`第X季` 与 `Season N`——取并集；**没有
表态的候选留着不猜**，因为误杀一整季的整包比漏掉一部剧更难被发现。
站点多于一个就是把同一份预算切成更小的片，每个站各自的召回因此下降。`1.0.0.7` 的配置
XML 里没有这几个键，仍然照常工作：清单缺席 = 用后端内置默认值。地址里带 `{q}`（云盘搜索页）
的站不参与任何磁力通道、也不占预算切片——它纯粹是云盘通道的入口。从网页解析来的云盘候选
以裸片名作为名字（`extract_share_entries` 记录的是链接，不是发行名），所以它们的画质分比
被删掉的 Pansou 路径粗——后者读的是搜索结果的 `note`。这是已知缺口，不是契约。

## 验证

- [B1] `tests` —— `tests/test_backend_retired_paths.py` 是本次新增的守卫：它的 AST 扫描
  在 `backend/` 下任何位置重新出现退役符号、关键字参数名或 CLI flag 常量时变红；同时钉住
  `speed_probe.py` 不存在、控制台不再有 `/api/verify`，以及 `source_config` 的契约
  （默认值、校验、上限、`{q}` 要求、`web_search_urls`）。它的 `CloudChannelWiringTests`
  把 CLI 调用换成记录器、真跑一轮 `console_server._run_search_job`，所以云盘通道是用行为
  钉住的、不是源码字符串断言：两个带网页地址的站 → 一次 `pan_search.py` 调用里带两个
  `--page`（片名已 URL 编码）；一个都没填 → 一次都不调用，并打印「云盘通道没有入口」。
- [B2] `tests` —— `tests/test_backend_sources.QueryLadderTests` 钉住锚点档的位置、配置顺序、
  中文片名覆盖与严格模式下同样运行；`run_search_main`/`EndToEndOfflineTests` 钉住站点清单
  与关键字来自环境变量，以及坏配置走 `SOURCE_CONFIG_ERROR` 退出。
- [B3] `tests` —— `tests/test_backend_scoring` 钉住陈旧的 `measured_speed_mbps` /
  `speed_unmeasured` 字段既不改分数也不改 breakdown；golden 表
  （`tests/fixtures/jmd_scoring/speed_cases.json`）已按存活下来的四档重写。
- [B4] `tests` —— `tests/test_backend_probe.ConsoleWiringTests` 与
  `tests/test_backend_integration` 保证控制台不再传测速开关，并保住 probe 的离线干跑。
- [B5] `source` —— `python3 -m py_compile backend/*.py` 加 `./build.sh`，因为插件与 C# 两侧
  的配置面都改了。
- [B6] `docs` —— README 的功能表、配置表与开发说明在同一次改动里更新，两份前驱决策记录
  也链到这里。
- [B10] `tests` —— 复核第一轮里那两条高严重度问题：非 API 站产出不了候选文件的那一轮，
  不再把**上一轮**的 `/tmp/<slug>.magnets` 喂给 probe（`CloudChannelWiringTests` 两个方向都钉）；
  那个文件不存在时 probe 也不再在读 `--extra-candidates` 之前退出（`MissingCandidateFileTests`），
  云盘候选因此在磁力侧出问题时仍然存活。
- [B12] `tests` —— 季号识别只有一个家。这次把正则、中文数字表与中文数字解析从
  `build_console`（它自己有一份 `SEASON_CODE_RE` / `SEASON_CN_RE` / `SEASON_EN_RE` /
  `_cn_number`）搬进 `search_pool`，与发行名解析放在一处；`build_console` 现在只负责排序
  后交给面板。`SeasonRecognitionHasOneHome` 在一批名字上比对两个调用方，并在那些名字
  重新出现在 `build_console` 时变红——把第二份贴回去见过它红。
- [B11] `tests` —— 严格按季收口的两个错误方向都钉住了：`SeasonDetectionTests` 覆盖三种显式
  写法与中文数字（`cn_number`），`StrictSeasonFilterTests` 钉住「`Sxx` 与『第二季』的别的季
  要被丢掉」以及「没有季标记的候选（整包、裸片名、第11集）必须留着」。两个方向都见过红：
  拿掉季词识别会挂检测用例，把「没表态」当成「别的季」会挂保留用例。
- [B13] `tests` —— 单地址配置页的契约，由随 `agent/config-page-layout` 分支一起合并进来的
  守卫钉住（`SiteUrlClassificationTests`）：裸主机补成内置站的默认搜索路径、带 `{q}` 的地址
  成为云盘页、分类不出来的地址**被拒绝**而不是被悄悄当成磁力站、显式声明 kind 的自建站照旧
  可用、配置页把它显示的 kind 原样往返、以及后端用同一个 `SERVICE_ID` 字面量自证身份。
- [B9] `tests` —— 云盘专用站的契约：`source_config` 接受只有 `web_search_url`、没有
  `base_url` 的站（两个入口都空的仍拒绝），`magnet_sites()` 把它挡在两条磁力通道之外；
  `tests/test_backend_sources` 钉住两个后果：这种站不会被非 API 通道问到，而站点清单里
  一个 `base_url` 都没有时会打印 `NO_MAGNET_STATION`、不发任何请求、以退出码 4 结束。
- [B8] `source` —— 2026-10-13 跑了一轮**真实**的（真索引、真 swarm，无桩）：
  `search_pool.py 漫长的季节 --publisher-only` 只剩两条配置的关键字查询
  （`[anchor] 漫长的季节 高清剧集网` → 7 页 61 条、5.4s，被 `--enough` 收工），发布者按名
  收口后 61 条全留；对这 61 条里最大的 6 条跑 `probe_magnets.py`，`stage_seconds =
  {health, metadata, live_check}`——**没有 speed 阶段**，`speed` 的 breakdown 是
  `{seeds_prior, seed_source, seeds, peers, mode, score_source}`，
  `live_check.reused_from_enrich = 1`（re-score 路径）。第 1 名 72.38 分且带磁力。记在这里是
  因为离线套件看不到真索引。
- [B7] `packaging` —— 两个新增配置键都用 `System.Xml.Serialization.XmlSerializer` 对着
  **构建出来的插件程序集**做了往返（Jellyfin 就是用它序列化 `BasePluginConfiguration`）：
  两个带全部四个字段的站点与两条关键字组合，序列化 → 反序列化后逐字段完好；而一份两个键
  都没有的 XML 反序列化出来是空清单——这正是「清单缺席 = 用后端内置默认值」对存量
  `1.0.0.7` 安装成立的原因。复现方式：建一个一次性 console 工程，引用本 csproj 与
  `Jellyfin.Controller`/`Jellyfin.Model` 10.11.11，把两个列表填好后序列化一个
  `PluginConfiguration`。同一个临时工程还用反射直接调了 controller 的形状校验：三个站
  （含一个云盘专用站）往返完好；校验放行「只有网页搜索地址」的站，拒绝「两个入口都空」、
  拒绝 `base_url` 旁边跟着未知 `kind`、拒绝不含 `{q}` 的网页搜索地址。这不是常驻守卫：
  本仓库没有 C# 测试工程。
