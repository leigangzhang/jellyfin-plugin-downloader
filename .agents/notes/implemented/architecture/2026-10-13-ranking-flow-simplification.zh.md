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

1. **`backend/source_config.py` 拥有这两份清单。** `JMD_BT_SITES`（JSON 数组，每项
   `{name, kind, base_url, web_search_url}`）与 `JMD_L1_KEYWORDS`（查询后缀数组）。
   `kind` 决定用哪段适配器——`diao` 是结构化搜索 API，`torrents-csv` 是聚合索引——所以
   加一个站是配置，加一种协议才是代码。空 = 用内置默认值：当前在用的两个站，加上实测
   有效的两条发布者署名。配置坏掉时报 `SOURCE_CONFIG_ERROR` 并以退出码 2 结束，**绝不**
   悄悄换成默认值。
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
4. **云盘通道 = 各资源站自己的 `web_search_url`。** 填了网页搜索地址（`.../search?q={q}`，
   必须含 `{q}`）的站会走已有的 `pan_search.py --page` 路径，控制台按站打印。一个站都没填
   时打印 `云盘通道没有入口`，而不是返回一个看不见的空。
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
之前一直是惰性的。严格按季的搜索会通过锚点档看到同一部剧的其它季——因为该档不带季标记，
而下游没有季标记过滤器。这一条记在这里，是为了让下一次改动是刻意为之而不是又一次意外。
站点多于一个就是把同一份预算切成更小的片，每个站各自的召回因此下降。`1.0.0.7` 的配置
XML 里没有这几个键，仍然照常工作：清单缺席 = 用后端内置默认值。从网页解析来的云盘候选
以裸片名作为名字（`extract_share_entries` 记录的是链接，不是发行名），所以它们的画质分比
被删掉的 Pansou 路径粗——后者读的是搜索结果的 `note`。这是已知缺口，不是契约。

## 验证

- [B1] `tests` —— `tests/test_backend_retired_paths.py` 是本次新增的守卫：它的 AST 扫描
  在 `backend/` 下任何位置重新出现退役符号、关键字参数名或 CLI flag 常量时变红；同时钉住
  `speed_probe.py` 不存在、控制台不再有 `/api/verify`，以及 `source_config` 的契约
  （默认值、校验、上限、`{q}` 要求、`web_search_urls`）。
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
