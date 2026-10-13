# 十一站生命周期

[English](README.md) | 中文

> 从 DeepSeek Harness 提炼的**最小实现**。一句话贯穿全程：
> **每一站产出一个"能判定"的东西。判不了的东西，不许进入下一站。**
>
> 每站的「闸门」列写的就是 [tools/workflow.json](../tools/workflow.json) 里真实存在的 check id —— 不是比喻。

本页同时是 `dev/` 的落地页：每一站的产物都住在下表那一行点名的目录里，而每站要遵守的规则是 `docs/` 下的常驻文档。

## 总表

| # | 站 | 产物（家） | 闸门 | 谁决定 |
|---|---|---|---|---|
| 1 | 意图 | 记录头部的 `Class:` 字段 | 人工 | 需求方 |
| 2 | 提案 | `.agents/notes/proposed/<class>/<date>-<slug>.md` | `decision-proposed`、`criteria-traced` | 提案者 |
| 3 | 决策 | `.agents/notes/implemented/<class>/<date>-<slug>.md` 或 `.agents/notes/rejected/<class>/<date>-<slug>.md`；能力登记 `dev/capabilities/registry.json` | `decision-implemented`、`no-proposal-era-headings`、`decision-rejected`、`capability-registry` | 提案者 + 评审 |
| 4 | 契约 | `dev/contracts/<slug>.md` + `dev/contracts/mirrors.json`（或源码接口文件 + 链接） | `contract-record`、`contract-mirror` | 接口 owner |
| 5 | 实现 | 记录里的 `## Consequences` / 任务条目 | `criteria-traced`、`change-scope --strict` | 实现者 |
| 6 | 验证 | 测试 + golden 文件，以及本次交付在 `dev/evidence/` 下的那唯一一份记录；策略在 `testing.md` | `testing-policy`、`no-time-based-test-sync`、`--self-test`、空语料规则、`criteria-traced`、`run-evidence` | 实现者 |
| 7 | 评审 | `dev/review/<date>-<slug>.md` | `review-record` | **人** |
| 8 | 集成 | 提交历史 + `change-scope` 报告 + 落地前 preflight | `change-scope --strict` | 人 |
| 9 | 发布 | `dev/release/<version>.md` + 不可变产物 | `release-record` | 人 |
| 10 | 演化 | `dev/upgrade-guide/<version>-<surface>.md`；规则在 [evolution.md](../docs/evolution.md) | `dev/upgrade-guide`、`upgrade-guide-budget`、`evolution-policy` | 人 |
| 11 | 退役 | `.agents/notes/archived/` + [manifest.json](../.agents/notes/archived/manifest.json)（封存清单 + 摘要 + 日期） | `archive-seal` | 人 |
| ⚡ | 事故（横切） | `dev/postmortem/<NNNN>-<slug>.md` | `postmortem-record` | 事故处理者 |
| 📄 | 文档（横切） | [documentation.md](../docs/documentation.md) + [docs/](README.zh.md) + [i18n.md](../docs/i18n.md) + [glossary.md](../docs/glossary.md) + [plain-language.md](../docs/plain-language.md) | `docs-policy`、`docs-budget`、`publish-manifest`、`i18n-policy`、`i18n-budget`、`generated-docs`（新鲜度经 `run-evidence`） | 人 |

## 三档采用建议

你不需要一次用满十一站。按你的项目当前处在哪个阶段选：

| 档位 | 装哪几站 | 适合 |
|---|---|---|
| **最小档** | 1 · 2 · 3 · 4 · 6 · 7，另加文档（documentation）、测试（tests）与能力登记（capabilities） | 一个人在快速迭代，还没有用户 |
| **交付档** | 上面 + 9 · 10（含 [evolution.md](../docs/evolution.md) 与 `dev/upgrade-guide/`） | 有对外接口、有别人在用 |
| **长期档** | 全部 + 5 · 8 · 11（含 `.agents/notes/archived/` 封存），外加否决记录（`.agents/notes/rejected/`）与事故（incidents） | 多轮演化、多人协作、要活很久 |

没装的站，对应的检查会**因为匹配不到文件而判无效**（见第 6 站）—— 这是刻意的：空壳检查比没有检查更糟。所以请在 [tools/tiers.json](../tools/tiers.json) 里把它声明为 `none`：开关会把这个站变成**声明的缺席**，只拥有它那个家的检查不再运行，空语料规则也不再适用于它们。指令就这一句 —— 这段原先列的八组映射现在归开关管。同时把目录删掉，因为开关陈述意图、树陈述事实：开关说缺席而文件还在是红，开关说已安装而家是空的也是红。

---

## 01 · 意图

**解决什么**：把"要什么"变成一个**可分类**的对象，让后面的规则能按类生效。

**进** → 有人提出一个需求或问题。
**出** → 它被归入一个 `Class`，并写进了某条记录（或 issue）。

**产物**：记录头部的 `Class:` 字段。类别用闭集：`feature` / `bug-fix` / `simplification` / `architecture` / `process` / `testing`。

**闸门**：人工 —— 分类是判断题，挡不住偷懒，只能靠纪律。

**反模式**：所有需求都走同一套重流程。**一个 bug fix 不需要写 Proposal**；给它套上完整流程，只会让人开始绕过流程。

---

## 02 · 提案

**解决什么**：在写代码之前，先写下**你要否决什么**，以及**怎么算做完**。

**进** → 非平凡改动启动。
**出** → `.agents/notes/proposed/<class>/<date>-<slug>.md` 存在，且：`Alternatives considered` **非空**；`## Acceptance criteria` 的**每一条**都带一个稳定编号，并在反引号里点名**它会为哪条检查变红**（`tools/workflow.json` 里已声明的 check id 或 surface 名）。

**产物**：[.agents/notes/proposed/TEMPLATE.md](../.agents/notes/proposed/TEMPLATE.md)

**闸门**：`decision-proposed`（缺任一段直接红）+ `criteria-traced`（漏编号、或点名一个不存在的检查 → 红）

**纪律**：一条标准的**落点**只有四种——断言、命令、快照、外部观测。四种都写不出来的，删掉，或者承认它是"品味"而不是验收。

**反模式**：把 Proposal 写成实现步骤清单 —— 那是任务拆分的活。Proposal 只回答"为什么做"和"做完是什么样"。

---

## 03 · 决策

**解决什么**：让"已落地的决定"和"还在讨论的提案"在物理上分开，并且**同一个决定只留一个事实版本**。

**进** → 提案实现。
**出** → 文件移进 `.agents/notes/implemented/<class>/`，并**改写为现在时**：`## Proposal` → `## Decision`；`## Acceptance criteria` + `## Risks` 折叠进 `## Consequences`，其中"被验证了什么"改写为 `## Testing` 并**保留原来的编号**。

**产物**：[.agents/notes/implemented/TEMPLATE.md](../.agents/notes/implemented/TEMPLATE.md)

**闸门**：`decision-implemented` —— 它要求 `## Decision`、`## Consequences` 与 `## Testing`，所以**没改写就红**；`no-proposal-era-headings` 再拦一道：移档后仍留着 `## Proposal` / `## Plan` / `## Migration plan` / `## Acceptance criteria` 也红。两个方向都堵上，是因为只要求"新骨架在"的话，一份记录可以同时挂着两套骨架。

**纪律**：编号是验收标准跨越移档的**唯一载体**。丢掉编号，就等于丢掉"这条标准由谁证明"。

**纪律**：事实可以原地改（路径、默认值随代码更新）；**决定不可以**。推翻一个决定要写新记录并交叉链接。

**反模式**：在旧记录后面追加"更新：后来又改成……"。历史叙述会被下一轮读者当成当前事实。

### 03.1 决策的一种特殊形态：能力要不要成形（seam）

有些决定不是"改不改这个函数"，而是"这个东西值不值得做成一个**可替换的能力**"。这一层单独有纪律和产物，因为它的错误代价最高：一个没有消费者的抽象会被后面的人当成基础设施依赖。

- **三个角色**：**Definition**（拥有接口与词汇）、**Provider**（实现它）、**Consumer**（只用 Definition，不看某个 Provider 的类型）。**一个角色不构成能力**：没有 Provider 的 Definition 是愿望，没有 Consumer 的 Provider 是负债，绑死在一个 Provider 上的 Consumer 不可替换。
- **不预测性拆分**：只有一个可想象的 Provider、一个 Consumer 时，三个角色就待在一个文件/包里，直到第二个出现。"将来可能需要"不是理由。
- **必须有当前消费者**：抽象、开关、兼容路径都要绑到一个**现在就在用**的消费者；没有就先别加。
- **不拆也要写理由**：`kind` 不是 `seam` 的条目，必须在 `note` 里写清"为什么它不是 seam"。

**产物**：[capabilities/TEMPLATE.md](capabilities/TEMPLATE.md) 定义的登记条目（写进 `dev/capabilities/registry.json`）。

**闸门**：`capability-record`（模板五段齐备）+ **`capability-registry`**（登记与实际**双向对齐**）：
- 每个能力在源码里有一行行首声明（默认 `# capability: <key>`）；
- 声明了没登记 → 未分类；登记了没声明 → 陈旧条目；
- `kind: seam` 必须同时给出 Definition、≥1 个 Provider、≥1 个 Consumer，且每条路径都要真实存在；
- 非 seam 的条目必须写出理由。

它和契约层是同一个模式：**存在性从源码发现，分类由人写，完整性由闸门咬死。**

**反模式**：为了"以后好扩展"提前把三个角色拆成三个包；或者登记表写完就再也不更新，让一份陈旧的能力地图继续冒充当前架构。

---

## 04 · 契约

**解决什么**：让文档**不可能悄悄过期**。

**进** → 有接口、类型、配置键或协议要对其他部分负责。
**出** → 二选一，且必须能判定：
- **指针**：接口住在源码 / schema 文件里（`.ts` / `.json` schema / OpenAPI），文档只写链接和一句话摘要；
- **镜像**：文档里粘贴了确切声明，且已在 `dev/contracts/mirrors.json` 登记，`contract-mirror` 逐字比对源码 `begin`/`end` 标记之间的区间。

**产物**：[contracts/TEMPLATE.md](contracts/TEMPLATE.md) 定义的五段记录（Interface / Source of truth / Projection / Check / Drift policy）+ `dev/contracts/mirrors.json` 的登记条目。

**闸门**：`contract-record`（五段缺一即红）+ `contract-mirror`（逐字比对、双向 1:1）；"这个粘贴有没有必要、写得对不对"这部分只能人工。

**纪律**：**登记与粘贴在同一改动内更新**。源码改了粘贴不跟 → 红；登记了块没了 → 红；出现没登记的块 → 红；镜像块被移出检查范围 → 红。

**反模式**：在文档里贴一份接口副本，然后忘了同步。**复述即漂移** —— 两份事实必然在某个时间点分叉。要么别贴（指针），要么贴了就登记并比对（镜像）。

*(DeepSeek Harness 的做法就是一例：`ts type-equiv` 用解析器从源码取声明与 JSDoc，和文档里的 ` ```ts type-equiv ` 块逐字比对；实测 471 个主块 + 471 个中文派生块、101 个源码文件全在册。)*

---

## 05 · 实现（"完成"的定义）

**解决什么**：定义"做完"。

**进** → 契约就绪。
**出** → **定义 / 实现 / 消费者**三者齐备，而且消费者是**当前就在用的**；缺任何一个就明确记为"未完成"，而不是"差不多能用"。

**判定表**（对着这次改动逐行过）：

| 手上有的 | 判定 |
|---|---|
| 一个接口 / 抽象 | **未完成**：至少要有真实的 Provider |
| 接口 + 一个实现，只有作者在调 | **未完成**：自消费不算消费者 |
| 三角色齐备，消费者绑死在某个实现上 | **未完成**：消费者只能依赖 Definition |
| 三角色齐备，但只有一个可想象的 Provider / Consumer | **不该拆**："将来可能需要"不是理由 |
| 三角色齐备，且角色以不同速率、因不同原因变化 | **成形的能力** |

**产物**：写进记录的 `## Consequences`，或任务条目里的一行"谁消费它"。

**闸门**：`criteria-traced`（每条验收标准都带编号、并点名归属检查）+ `change-scope --strict`（任何没有声明归属的文件会让它红）。"有没有当前消费者"是判断题，闸门拦不住 —— 所以它必须写进任务条目，由评审读。

**反模式**："差不多能用了"。一个没有消费者的能力不是半成品，它是**负债**：它会被读成"已完成"，然后在下一轮被当成基础设施依赖。

---

## 06 · 验证

**解决什么**：让"能跑过"和"按真实路径跑过"变成同一件事。

**进** → 实现存在。
**出** → 每条验收标准的编号都有一个**能变红**的归属；用户/模型可见的输出有**可回放的期望文件**；新守卫**有人见过它红**；跑过的证据按命令与结果被报出来，交付落地时在 **`dev/evidence/`** 下留下**唯一一份**记录（命令 + 三态结果：通过 / 未通过 / 无法判定），手工条目要么被真的做过，要么明确标成未验证。

**产物**：测试 + golden 文件，外加交付自己那一份 `dev/evidence/<slug>-<merge-base>.md`。**测试策略住在 [testing.md](../docs/testing.md)**（Tiers / Evidence per change / Test doubles / Determinism / Blocking and observational lanes / Prove a new guard / Flake policy 六段），LIFECYCLE 只管这一站的进出口。

**闸门**：
- `testing-policy`：`testing.md` 七段齐备且非空 —— 策略有唯一的家，而那个家有形状；
- `no-time-based-test-sync`：测试里不得出现固定等待（`setTimeout(` / `waitForTimeout(` / `sleep(`）—— 等待必须挂在状态上；
- `criteria-traced`：每条标准带编号、并点名一个已声明的检查或改动面 —— 把"改坏了哪条会红"变成必须写进文档的字段；
- `--self-test` + **空语料规则**：每种检查必须有正负样本；匹配不到文件的检查**直接判无效**（一个没有检查对象的检查就是空壳）；
- 改动面 `tests`：改测试文件时，`run-evidence` 会真的把那条命令跑掉，并把三态结果写进记录。

**先选层，再写测试**（细节见 [testing.md](../docs/testing.md#tiers)）：

| 层 | 它证明什么 | 它抓不到什么 |
|---|---|---|
| 单元 | 边界、错误路径、事件顺序、竞态 | 真实组合、真实入口 |
| 真实入口 | 从真实组合或真实产物启动一次，断言**世界**（不是自述） | 边界用例的性价比 |
| 可回放期望 | 可见输出录一份、回放比对；**每次 diff 人读**，当行为变更处理 | 没有可见输出的行为 |
| 覆盖率 | 每一行都有一个归属 | "行跑过了"不等于"功能对了" |

**纪律**：

- **新守卫必须能变红** —— 改坏一次，看它红，再改回来。没亲眼见过红灯的，不算守卫。
- **默认不信任方向** —— 每个"通过"先问两件事：是谁说的、我读的是外部状态还是它自己的自述。交付前过一遍 [.agents/skills/apply-distrust/SKILL.md](../.agents/skills/apply-distrust/SKILL.md) 的 8 个问题；不信任只花在跨边界和信号可能骗人的地方。
- **确定性是测试的一部分**：原子分配资源、按状态同步（不睡）、包住进程级全局状态、按跑道给超时预算。
- **flake 不是噪声**：调超时、加重试、全串行、减弱断言、加 sleep 都不算修复；只有"外部 provider 的短暂失败"可以在那个边界上重试。
- **车道要标严重性**：每条车道要么阻塞、要么观测。平台相关的已知不稳定项**降级为观测**，不删、不弱化；观测项必须汇报，并且要写出"连续多少次绿就晋升回阻塞"（否则它会腐烂成没人看的清单）。见 [testing.md](../docs/testing.md#blocking-and-observational-lanes)。

**反模式**：只测 happy path；写一条永远为真的断言来"提高覆盖率"；把不稳定当噪声重试掉。

---

## 07 · 评审

**解决什么**：在"作者"和"裁判"之间插一道**独立的证据要求**。

**进** → 改动可运行。
**出** → `dev/review/<date>-<slug>.md` 存在；`## Evidence` 是**外部观测**（命令、输出、文件状态），不是"我看了一遍"；每条发现都在 `## Follow-up` 里写下了归宿。

**产物**：[review/TEMPLATE.md](review/TEMPLATE.md)

**闸门**：`review-record`

**纪律**：**发现必须写下归宿**，三选一 —— **变成一条检查**（同改动带上负控制）/ **变成一条规则**（判不了的那类，写进对应文件）/ **刻意不做**并写明为什么（一次性、不可复现、维护成本大于收益）。"刻意不做"是允许的；**不记录才是问题** —— 没有归宿的发现只会被重新发现一遍。

**反模式**：让写代码的那个 agent 自己宣布"已审查，无问题"。**同源评审等于没有评审** —— 它只是把同一个盲区说了两遍。

---

## 08 · 集成

**解决什么**：让多个改动按依赖顺序、可复验地落地，并且**落地用的证据是落地这一刻的**。

**进** → 评审通过，改动可运行。
**出** → 变更按依赖顺序落地；**落地后重跑受影响面的证据**；每一条"已解决 / 已通过"都有一个**落地后**的确认。

**产物**：提交历史本身 + `change-scope` 报告 + 一份**落地前 preflight**（重取 exact base/head、重读 diff）。

**闸门**：`change-scope --strict` + 重跑第 6 站验证。`--strict` 让"没有归属的新文件"变成失败，而不是被忽略。

**纪律**：

1. **落地前 preflight。** 合并前重新解析 base 与 head，不信分支名、也不信上一次报告。每个改动**独立判断就绪**：顶部绿不代表它下面的层绿，一个聚合的全绿也不构成任何单面的证据。
2. **任何历史重写都会作废旧证据。** rebase / amend / squash 之后，之前的"已通过、已解决、已审批"全部失效。重新拉取 exact head、重读 diff、重跑受影响面的证据，然后才谈就绪。
3. **重写要显式选策略。** merge-forward（保留合并检查点、不改写历史）与 rebase（改写历史）都允许，但要有意识地选。改写后的 push 只能带 lease 保护（`--force-with-lease=<branch>:<observed-oid>`），远端一旦动过就中止；**禁止裸 `--force`**。
4. **一次落一个能二分的小步。** 出问题时能定位到具体的层，而不是回退一整坨。
5. **收尾是独立的最后一步。** 删除分支、临时产物或独占资源之前，先确认没有其它东西还以它为依赖；有依赖就停，先解决依赖。

**反模式**：一次合一大坨（出问题时无法二分定位）；重写历史后仍拿旧结果当证据；删除分支 / 产物之前没确认依赖；把"聚合全绿"当成某一层已就绪的证明。

---

## 09 · 发布

**解决什么**：把"在我机器上能跑"变成"**在仓库之外，拿发布的同一份字节，也能跑**"。

**进** → 集成完成，改动落在一个确切的 revision 上。
**出** → `dev/release/<version>.md` 存在，且：

- 它点名**确切的发布 revision 与不可变产物**（`## Revision`）；
- `## Evidence` **按执行顺序**列出：**演练**（无凭证构建 → 仓库外干净安装）→ **发布**（显式人工动作）。中断一次跑，留下的是一段能读的前缀，而不是一堆无顺序的结果。

**产物**：[release/TEMPLATE.md](release/TEMPLATE.md) + 一份不可变产物（tarball / 构建目录 / 镜像）。

**闸门**：`release-record`（`Revision` / `Scope` / `Evidence` / `Breaking changes` / `Rollback` 五段齐备且非空）。

**纪律**：

1. **演练与发布分离。** 发布等价的构建与仓库外安装**不需要任何发布凭证**，所以每次改动都能演练；真正的发布动作是**显式的人工一步**，不该伪装成一个自动检查，也不该因为它绿了就自动发生。
2. **发布边界是不可变产物。** 先产出一份不可变产物，再验证它，再发布它。**发布步骤不重新构建** —— 它上传的必须就是演练验证过的那份字节；一构建就可能发出一份没人验证过的内容。
3. **干净安装要清掉宿主环境。** 只换目录不够。用一次性 `HOME` / 配置目录与 plain runtime，并显式去掉 `NODE_OPTIONS`、`NODE_PATH`、语言路径、全局 git / npm hook 这类会从宿主渗进来的变量；否则"干净安装"只是换了个工作目录的普通安装。
4. **发布要幂等。** 发布前查已发布状态与内容摘要：**缺了才发；已存在且摘要相同就跳过**（重跑安全）；**摘要不同则失败** —— 同一个版本覆盖了不同内容，是缺陷，不是重试。
5. **记录确切 revision。** 发布记录点名 tag / revision 与产物摘要。内容变了就换新 revision，绝不覆盖同一个版本。

**反模式**：用仓库内的路径跑一遍就算发布了（仓库内的成功会被 `node_modules`、本地缓存、未提交文件、残留产物目录**同时**掩护 —— 只有仓库外的干净安装能一次性排掉它们全部）；发布时重新构建（发的就不是验证过的字节）；同一个版本覆盖不同内容；把"演练通过"当成"已发布"。

*(DeepSeek Harness 的对应做法：`release:verify` 断言必须从族 tag 运行，`release:pack` 无凭证地把一个 commit 打成不可变 tarball，`release:verify-packed-install` 把它们装进仓库之外的一次性消费者目录、用 plain Node 驱动，`release:publish` 只上传 pack 产出的字节、且按 registry 的已发布摘要决定"发 / 跳过 / 失败"。发布 workflow 是 `workflow_dispatch` 手动触发，从不作为 PR 检查出现。)*

---

## 10 · 演化

**解决什么**：破坏性变更必须留下"**读者怎么迁**"。

**进** → 改动会破坏**已经被使用过**的东西（接口、配置键、数据格式、命令行）。
**出** → `dev/upgrade-guide/<version>-<surface>.md` 包含 `## Change` 与 `## Migration`，且 Migration 的**最后一步是"怎么确认迁移成功"**。

**产物**：[upgrade-guide/TEMPLATE.md](upgrade-guide/TEMPLATE.md)

**闸门**：`dev/upgrade-guide`（两段齐备）+ `upgrade-guide-budget`（行数上限）+ `evolution-policy`（规则的家有形状）

**规则住在 [evolution.md](../docs/evolution.md)**：改了什么 → 写哪份记录（选择器）、三种版本状态分开记（writer / 已确认基线 / 已发布）、已消费的产物只增不改。本文件只管这一站的进出口。

**反模式**：把它写成 CHANGELOG（读者只关心"我要改什么"）；一个版本里坏了几件事却只写一份指南 —— 一份指南只对一个面负责。

---

## 11 · 退役

**解决什么**：让旧东西**真的会消失**。

**进** → 一份产物失去价值，或开始误导读者。
**出** → 删除，或移进 `.agents/notes/archived/<class>/`、头部加 `Archived: <yyyy-mm-dd>`、登记进 [manifest.json](../.agents/notes/archived/manifest.json)，**此后不再编辑**。

**产物**：`.agents/notes/archived/` 目录 + [manifest.json](../.agents/notes/archived/manifest.json)（清单 + 内容摘要 + 归档日期）。

**闸门**：`archive-seal` —— 未登记的掉落、封存后被改的文件、清单与头部日期不一致，都红。

**规则住在 [evolution.md](../docs/evolution.md)**：删 / 合并 / 留下被否记录 / 封存的选择标准，以及"封存之后要改内容该怎么办"。

**反模式**：什么都留着。旧文档不会安静地待着 —— 它会以"事实"的身份出现在下一轮的上下文里。

---

## ⚡ · 事故（横切十一站）

事故不属于任何一站，它是**所有站的输入源**。

**进** → 一个 bug 到达了它不该到达的地方（真实用户、已合并的代码、已发布的版本）。
**出** → `dev/postmortem/<NNNN>-<slug>.md`，且 `## Negative control` 证明了新加的闸门确实会为这个事故变红。

**产物**：[postmortem/TEMPLATE.md](postmortem/TEMPLATE.md)

**闸门**：`postmortem-record`

写它的判据（三条同时满足）：机制**不明显**、原因**系统性**（是测试/工具/约定的缺口，不是一次手滑）、**重新发现它的代价很高**。

---

## 📄 · 文档（横切十一站）

文档不是第 12 站，它是每一站的输出面：提案变成决策记录、接口变成契约、验证变成证据、破坏性变更变成升级指南。规则本身住在 [documentation.md](../docs/documentation.md)，这里只写它怎么横切。

**进** → 有一条事实要对别人负责（读者、插件作者、升级者，或下一轮的 agent）。
**出** → 它落在**恰好一个家**里，并且是三种形态之一，判据可查。**文件存在 ≠ 会发布**：能否被消费者取走由 [docs/publish.json](../docs/publish.json) 显式决定。

| 形态 | 什么时候用 | 把住它的闸门 |
|---|---|---|
| **链接**（默认） | 事实住在源码、配置或生成器里 | `prose` 面；链接检查目前是占位 evidence，换成你自己的 |
| **镜像** | 必须逐字展示一份声明 | `contract-mirror`：逐字比对 + 登记与粘贴双向 1:1 |
| **生成** | 整页要从唯一源穷举导出 | `generated-docs` 面上的 `--check`，由 `run-evidence` 执行 |

**闸门**：
- `docs-policy`：标准有唯一的家，且这个家七段齐备（kind / 归属 / 教程与参考 / 三种形态 / 预算 / 发布 / 防劣质清单）；
- `docs-budget`、`contract-kinds-budget`、`i18n-budget`：常驻文档是预算，按行或按词；
- `publish-manifest`：每个 Markdown 文件**恰好**归 `public` 或 `internal` —— 谁都没归的是静默漏登记，不是"没发布"；`public` 指向空文件也是红；
- `generated-docs`：生成页只读，改生成器、重跑，由 `run-evidence` 证明它没过期。

**纪律**：
- **一个事实一个家**：同一句话出现两次，删一处、链接另一处；复述即漂移。
- **先分类再动笔**：tutorial 是路径，reference 是查找范围；kind 决定骨架、预算和读者。
- **当前态**：历史住在决策记录与事故复盘里，当前态文档只说现在是什么。
- **超预算先搬家**：搬家 → 压缩 → 最后才提数字，提数字要写理由。

**反模式**：文件存在就以为会发布；手改生成页；在文档里再抄一份源码或生成器已经拥有的清单；提预算数字而不是把内容搬去它该在的家。

---

## 每站唯一要问的那个问题

| 站 | 问自己 |
|---|---|
| 1 意图 | 这条需求属于哪一类？（决定后面哪些规则对它生效） |
| 2 提案 | 我否决了什么？每条验收标准的编号与归属检查都写了吗？ |
| 3 决策 | 这是"事实变了"还是"决定变了"？这个能力有当前消费者吗、三个角色齐了吗？ |
| 4 契约 | 接口写成了机器能比对的形式吗？还是我在复述？ |
| 5 实现 | 定义 / 实现 / 消费者，三段齐了吗？谁**当前**在消费它？ |
| 6 验证 | 每条标准的编号都有一个会变红的检查吗？我真的见它红过吗？这条测试在真实 CI 拓扑下还对吗？ |
| 7 评审 | 评审者是不是和作者同源？证据是外部观测吗？ |
| 8 集成 | 落地前 preflight 了吗？重写历史之后，我重新确认了吗？ |
| 9 发布 | 在**仓库之外**装一次、跑一次了吗？我发的是验证过的那份字节吗？ |
| 10 演化 | 读者怎么迁？怎么知道迁成功了？三种版本状态分别记在哪？ |
| 11 退役 | 哪份旧文档现在会误导下一轮？它该删、该合并，还是该封存？ |
| 📄 文档 | 这条事实的家是谁？要贴的是原文还是链接？它归 `public` 还是 `internal`？这正是本套件对该概念的用词吗？ |
