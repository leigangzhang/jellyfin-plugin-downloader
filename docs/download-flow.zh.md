# Jellyfin Downloader：下载资源链路（模块粒度）

[English](download-flow.md) | 中文

链路分三层、12 个阶段，从 Jellyfin 详情页一直走到媒体库里的规范文件。逐阶段的图、阶段卡与失败分支都在 [download-flow.html](download-flow.html) —— narrative 只有那一个家。本页只放可机检的事实：模块清单、链路口径、以及刻意保留的东西。

## 一句话链路

`入口 → 同源代理 → 建搜索任务 → 查重 → 磁力与云盘分享 → probe 打分 → 快照与面板轮询 → 提交 → 下载监控 → 校验归档 → 库刷新`

## 模块清单

| 层 | 模块 | 执行动作 |
|---|---|---|
| 插件（C#） | `Controllers/JellyfinDownloaderController.cs` | 注入分片、配置、季列表、后端生命周期、`/api/**` 同源代理 |
| 插件（C#） | `BackendManager.cs` | 拉起后端、端口自增、`SERVICE_ID` 校验、注入 `JMD_*` |
| 插件（C#） | `MediaPaths.cs` | 解析媒体根与同盘 `.staging` |
| 插件（C#） | `Web/inject_0*.js`（5 个分片） | 入口按钮、面板、搜索请求、候选渲染 |
| 后端 | `console_server.py` + `console_server/` | HTTP 入口、契约常量、路由、搜索任务、快照、子进程运行器 |
| 后端 | `search/` + `torrents_csv_source.py` | 资源站阶梯与 torrents-csv 第二通道 |
| 后端 | `pan/pan_search.py`、`pan/pan_pool.py` | 云盘分享链接与云盘候选，进同一套打分 |
| 后端 | `pan/pan_transfer.py` | 云盘取回状态机：`plan` / `verify-transfer` / `set-save-path` / `verify-retrieve` / `watch` / `status` |
| 后端 | `probe/` + `candidate_score/` | 健康度、metadata、加权打分、排名 |
| 后端 | `download/` | 查重、提交迅雷、watcher、六层校验、问题处置、清理 |
| 后端 | `media_download_lib/` | 迅雷库只读、命名、同盘原子移动、归档 |

## 搜索状态与退出码

`console_server.py` 把 probe 的输出映射成状态名；同样的 token 会出现在 job 日志里。

| 状态 | 含义 |
|---|---|
| `ok` | 本轮交付了候选 |
| `best_below_min_score` | 有排名，但第 1 名低于阈值 |
| `best_dead_swarm` | 第 1 名是死种，交付了也解析失败 |
| `swarm_dead` | 拿不到 metadata 且没有外部做种者：种子死了 |
| `metadata_failed` | 拿不到 metadata 但外部 peers 存在：网络或 tracker 问题 |
| `no_eligible_resource` | 候选都被结构性否决 |
| `probe_partial` | 软预算耗尽，结果不完整 |
| `rc_N` | 脚本非零退出（例如站点配置坏掉时的 `rc_2`） |

## 冗余与保留

以下来自一次只读审计。标「已删除」的行已从代码树移除，其余是刻意保留的。

| 项 | 结论 |
|---|---|
| `media_download_lib/paths.py` 的 `thunder_parallelism()` | 死代码：除自身文件外无引用；已删除 |
| `/api/pan/*` + `pan_transfer.py` | 刻意保留：云盘取回路径继续存在 |
| `/api/cleanup`、`/api/mark`、publish 端点 | 只有后端自己能到达；本仓库没有客户端；已删除 |
| `data-jdl-header`、`data-jdl-key` | 只写不读的属性；已删除 |
| `/python/status`、`/python/install` | 孤儿端点；配置页已不再提供安装入口；已删除，`PythonInstaller.cs` 保留为安装指引 |
| `BackendBaseUrl`、`BackendScript` | 保留：把插件指向另一个后端的唯一途径 |
| `BackendManager.Status()` 的额外字段 | 对外 JSON 契约；本仓库内无读取者 |

## 未核实项

- 面板的「最低分阈值」是否回写插件配置。
- 一轮 probe 的真实墙钟耗时：`console_server.py` 写明该数字是推算，不是本机实测。
- 本仓库之外是否有东西在消费 `/api/mark`、`/api/cleanup` 与 `Status()` 的字段。

记录于 commit 2d7da69。可视化真相源：[download-flow.html](download-flow.html)。
