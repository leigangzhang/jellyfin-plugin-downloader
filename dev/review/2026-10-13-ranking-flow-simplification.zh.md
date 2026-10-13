# Review: 1.0.0.8 取源与选种简化 —— teammate 两轮复核（2026-10-13）

[English](2026-10-13-ranking-flow-simplification.md) | 中文

## 主张

1.0.0.8 的简化声称：资源站清单与 L1 关键字组合是**配置**而不是常量；候选池只留两条通道
（资源站磁力、以及资源站网页搜索喂给云盘）；配置的关键字是**第一个**查询档位；打分链是
`gather → 健康度 → metadata → 打分 → re-score → rank`，且 aria2c 测速已删除。

## 证据

复核者：AgentTeams 搭档 `verifier`，在**隔离副本**里工作（`/tmp/jmd-review` = `c0ba28a`，
`/tmp/jmd-review2` = `7d4b976`，都已断开 origin、对本工作区无写权限）；两次复核期间两个副本的
`git status --porcelain` 都是空的。本记录转写它的两份报告（团队归档里 `t1` / `t2` 的 output）；
队长是这次改动的作者，只写了下面的处置结论。

- 副本里跑 `python3 -m unittest discover -s tests` → 第一轮 `265 tests OK`、第二轮 `273 tests OK` ——这次改动没有打坏自己的套件。
- 用五条候选的夹具跑 `search_pool.py … --season 1 --strict-match` → 第一轮只丢掉了显式 `S02` 那一条（`STRICT_SEASON_FILTER {kept:4, dropped:1}`）；第二轮在同一份夹具、当前树上得到 `{kept:1, dropped:4}`，「第二季」/`Season 2`/`第2季` 也一起被丢掉。
- 一个「所有资源站都写不出候选文件」的轮次 → 第一轮实测 `EXIT=4`，且 `/tmp/stale.magnets` 前后 字节完全一致（md5 不变）；probe 在读 `--extra-candidates` 之前就返回 `2`。
- `probe_magnets.py` 传一个不存在的 `--candidate-file` 但给有效的 `--extra-candidates` → 第二轮实测不再 `rc=2`，云盘候选进入 `ranked`。
- 队长用**禁用字节码**（`python3 -B` 且先清 `__pycache__`）重跑九处变异 → 每条守卫都按预期见红； 同样的变异在字节码被缓存时给出过互相矛盾的结果，这正是「刻意承担的风险」里记的那次纠正。

## 发现

第一轮针对 `c0ba28a`，第二轮针对 `7d4b976`；除标注「未修」的以外全部关闭。

- **阻塞项（第一轮，`7d4b976` 关闭）** ——「所有资源站都是云盘专用站」的那一轮会丢掉全部云盘候选： `search_pool.py` 以 4 退出且不重写候选文件，控制台仍把固定的 `/tmp/<slug>.magnets` 传给 probe， 而 `probe_magnets.py` 在读 `--extra-candidates` 之前就返回 2。影响：唯一保留下来的云盘通道什么都 交付不了。证据：`EXIT=4` 且那份陈旧文件 md5 不变。
- **阻塞项（第一轮，`7d4b976` + `237ee8b` 关闭）** —— 同一个固定路径让 probe 读到**上一轮**的候选。 影响：把陈旧结果当本轮结果呈现。证据：该文件跨轮次落盘内容一模一样；现在改成按轮唯一 （`/tmp/<slug>.<jid>.magnets`）再加一道轮次判定。
- **阻塞项（第一轮，`da91366` 关闭）** —— 严格按季形同虚设：锚点档在严格分支之前生成，下游也没有 按季过滤。影响：按第 1 季搜索会浮出别的季。证据：上面那次五条候选的实测。
- **阻塞项（第二轮，在被报版本的后继里已经修掉）** —— 收口只认 `SxxEyy`/`Sxx`，于是「第二季」与 `Season 2` 漏网。证据：第二轮 `{kept:4, dropped:1}`，`da91366` 之后是 `{kept:1, dropped:4}`。
- **建议（第二轮，`237ee8b` 关闭）** —— 退役名字的 AST 守卫可被 `getattr(o, "measured_speed_" + "mbps")`、`"".join([...])`、`%` 格式化、`import speed_probe as sp` 绕过。影响：退役路径可能悄悄 回来而没人发现。证据：第二轮用这些写法实测全绿。
- **建议（第二轮，`237ee8b` 关闭）** —— 严格按季的收口只在 `main()` 末尾跑，而 `collect` 每完成一个 关键词就落一次盘；被杀的那一轮会留下未收口的文件。
- **建议（第二轮，`237ee8b` 关闭）** —— `CANDIDATE_LINES_SKIPPED` 把用户文件里最多 60 个字符抄进 任务日志；「仅对云盘候选打分」那句在云盘候选文件被读之前就打印了。
- **阻塞项（第一、二轮，由另一个会话在 `agent/config-page-layout` 上关闭，合并为 `0cd4b4a`）** —— 保存配置会无条件重启后端，而 `BackendManager.KillByPort` 会杀掉该端口上的每个 pid，于是把端口填成 Jellyfin 自己的就是杀掉 Jellyfin；`POST {"btSites":[null]}` 会 500；C# 的 `IsHttpUrl` 接受了 Python 侧拒绝的值。那个分支把「杀进程」换成端口递增探测、让后端带 `SERVICE_ID` 自证身份（外部 HTTP 服务不再被误认成它）、把分类不出来的地址**拒绝**而不是兜成 `diao`，并补上配置页需要的 kind 往返。

## 归宿

- **变成了一条检查（check）** —— `tests` 面：`CloudChannelWiringTests.test_a_stale_candidate_file_is_not_fed_to_the_probe` / `test_each_round_gets_its_own_candidate_file`、`MissingCandidateFileTests`、 `StrictSeasonFilterTests.test_a_killed_round_lands_already_filtered_candidates`、 `SeasonDetectionTests`、`SeasonRecognitionHasOneHome`，以及 `RetiredFragmentGuardTests` 里的 字面量/拼接/import 用例。
- **变成了一条规则** —— 决策记录 `2026-10-13-ranking-flow-simplification`（后果段：严格按季由显式 季标记收口；验证段 `[B10]`–`[B12]`），以及 AST 守卫自己的 docstring：写明它抓不到什么。
- **刻意什么都不做** —— `"".join([...])`、`%` 格式化、`bytes`、先把名字拼进变量这类写法守卫抓不到； 静态穷举它们的维护成本高过它们藏住的缺陷，而它们能复活的那些退役路径已有行为测试覆盖。
- **在所归属的那个界面上修掉了** —— 配置页 / Controller / `BackendManager` 里的发现当时只报告不打补丁，另一个会话的分支（`agent/config-page-layout@f801a37`）把三条全关了；把它们合进来的那次合并只遇到两处纯注释冲突，两侧都保留。

## 刻意承担的风险

- 服务端的严格按季收口对快照是**不可逆**的：明确标了别的季的候选根本到不了面板，任何开关都放不回来。 接受它，是因为面板自己的季规则本来就把这类候选挡在默认视图之外。
- **没表态**季号的候选留着，所以「含整季包」下仍可能看到属于别的季的整包。接受它：靠猜会悄悄丢掉 一整季。
- 按轮命名的候选文件会留在 `/tmp`，不再互相覆盖。
- **队长自己的变异证据这一轮错了两次**（同尺寸改动被 `__pycache__` 里的旧字节码顶掉；更早还有一个 原型夹具把 dict 套进了 list）。本记录里每一条「见过它红」都是清掉字节码缓存、用 `python3 -B` 重跑过的；没这样重跑过的一律不在这里声称。
