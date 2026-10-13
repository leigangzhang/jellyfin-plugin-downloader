# 文档标准（documentation.md）

[English](documentation.md) | 中文

本仓库的文档标准。规则对任何仓库成立；文中点名的命令是本套件自己的，所以每一条都真能跑。阶段进出口在 [dev/README.md](../dev/README.zh.md#--文档横切十一站)；本文件 owns 规则本身。

**技能（skill）**是唯一一种读者是模型的 kind：它的 `description` 是**加载条件**（`Use when …`）而不是自我摘要；正文承载决策逻辑；最后一节点名要跑的闸门。校验逻辑不住在技能里。加载器由宿主提供，而 `skill-trigger` 只证明 description 仍是一个触发条件，从不证明它被加载过。

## 文档的 kind

每份文档先归一个 kind，kind 决定它的骨架、预算和读者。kind 由**机械事实**推导（它回答什么问题、谁读它），不由目录名决定。

| kind | 它回答什么 | 不属于它 |
|---|---|---|
| 指南（[kit.md](kit.zh.md)、[dev/README.md](../dev/README.zh.md)、本文件、[testing.md](testing.zh.md)、[i18n.md](i18n.zh.md)、[evolution.md](evolution.zh.md)） | 一次学习路径，或一份当前规则 | 逐条复述它所链接的家的内容 |
| 索引（[docs/README.md](README.zh.md)） | 去哪找什么 | 任何规则或事实本身 |
| 生成（[docs/check-catalog.md](check-catalog.md)） | 从唯一源穷举导出的事实 | 任何手写内容 |
| 契约（[dev/contracts/TEMPLATE.md](../dev/contracts/TEMPLATE.zh.md)、[dev/capabilities/TEMPLATE.md](../dev/capabilities/TEMPLATE.zh.md)） | 一份要对调用方负责的接口 | 行为叙述与理由（→ 决策记录） |
| 记录（[.agents/notes/](../.agents/notes/implemented/TEMPLATE.zh.md)、[dev/review/](../dev/review/TEMPLATE.zh.md)、[dev/release/](../dev/release/TEMPLATE.zh.md)、[dev/postmortem/](../dev/postmortem/TEMPLATE.zh.md)） | 一次决定、评审、发布或事故 | 当前态规则（→ 各自的规则家） |
| 技能（[.agents/skills/](../.agents/skills/README.zh.md)） | 什么时候做什么 | 判定逻辑（→ 闸门）与契约（→ 源码或 `dev/contracts/`） |
| 模板（`*/TEMPLATE.md`） | 一份记录该长什么样 | 真实内容 |
| reference（[glossary.md](glossary.zh.md)、[plain-language.md](plain-language.zh.md)） | 按名字或术语查的当前事实 | 教学路径、决策理由、生成式目录 |

新增一个 kind：同时给出它的骨架（`required-sections`）、它的家（一个目录或 `patterns`）和一条把文档映射到它的检查。三者缺一，读者就无法判定这份文档是否合格。

**命名。** 大写只给"有工具或读者按名查找"的文件：`README.md`、`AGENTS.md`、`CLAUDE.md`、`SKILL.md`、`TEMPLATE.md`。其余一律小写 kebab-case，政策文档也不例外。名字只是标签，永远不是 kind 的信号：kind 由机械事实决定，所以换了 kind 是改名。

## 一个事实一个家

一句事实只住一个家；其他地方给链接。放置判据：

| 一条事实 | 它的家 | 不属于 |
|---|---|---|
| 常驻规则（每个任务都要在上下文里） | [AGENTS.md](../AGENTS.md) | 故事、例子、情境流程 |
| 阶段进出口 | [dev/README.md](../dev/README.zh.md) | 逐条检查清单（→ `tools/workflow.json`） |
| 某一站的记录 | `dev/<station>/`（见 [dev/README.md](../dev/README.zh.md)） | 该站的规则（→ `docs/`）或决定的理由（→ [.agents/notes/](../.agents/notes/implemented/TEMPLATE.zh.md)） |
| 验证策略 | [testing.md](testing.zh.md) | 具体测试命令（→ 改动面的 evidence） |
| 演化与退役规则 | [evolution.md](evolution.zh.md) | 某次破坏的具体迁移步骤（→ `dev/upgrade-guide/`） |
| 文档规则 | 本文件 | 产品契约（→ README 或源码） |
| 为什么这么选、放弃了什么 | [.agents/notes/](../.agents/notes/implemented/TEMPLATE.zh.md) | 当前态规则 |
| 语言、配对与术语译法 | [i18n.md](i18n.zh.md) | 一个词是什么意思、该用哪个（→ [glossary.md](glossary.zh.md)） |
| 一个词是什么意思、该用哪种写法 | [glossary.md](glossary.zh.md) | 译法对（→ [i18n.md](i18n.zh.md#术语)） |
| 接口、类型、配置键 | 源码里的声明 | 文档里的副本，除非登记为镜像 |
| 检查引擎声明的 kind | 登记为镜像的 [dev/contracts/kinds.md](../dev/contracts/kinds.zh.md)（该 seam 的 definition） | 拿 provider 的文件当可读定义，或第二份副本 |
| 一个能力的角色 | `dev/capabilities/registry.json`，由 `capability-registry` 绑定到 `# capability:` 标记 | 只在标记里写角色，或第二份登记表 |
| 项目跑在哪一档 | `tools/tiers.json`，档位被声明的唯一地方，由 `tier-manifest` 读取 | 恰好存在的某个目录，或被人手注释掉的检查 |
| 谁能被发布 | [docs/publish.json](publish.json) | 任何第二份白名单 |
| 闸门实际在查什么 | `tools/workflow.json` | 手抄的检查清单（→ 生成的 [docs/check-catalog.md](check-catalog.md)） |

**复述即漂移**：同一句话出现两次，删一处、链接另一处。机械可查的复述钉成 `forbidden-regex`（见 [The slop checklist](#防劣质清单)）。

## 教程还是参考

每份文档按**用途**二选一，不按路径：

- **tutorial**：一条通往结果的有序路径，只介绍每一步当下需要的概念；
- **reference**：一个查找范围，描述当前行为，没有教学顺序。

两者都重就拆成两份；只有一小块是另一种形态，就在节内标注。先写读者的起点、可观测的结果、最可能的失败与恢复路径，再写细节。教程按前置依赖排序，不按实现顺序。

## 生成、镜像还是链接

一个事实进入文档只有三种方式，默认第三种：

1. **链接**：事实住在源码、配置或生成器里，文档只写它是什么、去哪看。
2. **镜像**：必须逐字展示时，文档围栏块 + `dev/contracts/mirrors.json` 登记，由 `source-mirror` 逐字比对源码 `begin`/`end` 标记之间的区间。登记与粘贴必须在同一改动内更新。
3. **生成**：整页由生成器从唯一源导出。生成页只读：改生成器、重跑生成命令，绝不手工修补。用这条之前先问「链接或镜像够不够」。

生成页的权威性来自**生成器 + 新鲜度闸门**，不来自文本本身。所以每份生成页都要在 `tools/workflow.json` 的改动面上声明一条 `--check` 命令，由 `run-evidence` 真的跑掉并写下三态结果。本套件的活样例是 `docs/check-catalog.md` 与 `tools/gen-docs.py --check`，以及每一对双语文档与 `tools/pair-docs.py --check`。

（DeepSeek Harness 的同类实现：`gen-doc-graphs.ts --check` 逐字比对生成集。它的另一条生成纪律是「同源多投影」——同一个生成器同时产出文档与运行时代码，所以文档不会腐烂。双语配对是第四种形态，规则有自己的家 —— [i18n.md](i18n.zh.md)。）

## 预算

常驻文档是预算，不是仓库。超预算时按顺序处理：

1. **搬家**：内容属于另一个家 → 移过去，留一行链接；
2. **压缩**：它确实属于这里，但可以更短；
3. **提数字**：只有文字真的需要空间时才提，并在 commit 里说明理由。

`budget` 检查按行（`maxLines`）或按词（`maxWords`，空白分隔，同 `wc -w`）。没有词间空格的正文按词计会严重低估，所以中文常驻文档用行预算、英文侧用词预算 —— 一侧一条，`docs-budget` 与 `docs-budget-zh` 就是例子。阈值是护栏不是削减目标：离上限 5% 以内就先搬家或压缩，不要继续加字。

## 发布

**文件存在 ≠ 会发布。** [docs/publish.json](publish.json) 是唯一的发布开关，把 `publish-manifest` 语料里的每个文件归成 `public` 或 `internal`：

- `public` 逐条**点名文件**，不接受通配：发布是对单个文件的承诺，不是对一个目录的承诺，新页面必须显式加进来；
- `internal` 可以按家（目录）声明，并写明它为什么留在仓库；暂时是空货架没关系，那不是承诺；
- 每个文件必须**恰好**命中一条声明：谁都没命中的是**静默漏登记**，同时命中两条是歧义，两者都红；
- `public` 名下没有真实文件是坏链，红。

清单是投影的开关，不是投影本身：消费者从这些 `public` 文件构建，仓库 Markdown 始终是唯一可编辑源。

## 防劣质清单

机械可判的钉成检查，判不了的留给 review。逐条搜：

- **重复的规则**：用一句独特的短语搜全文，留一个家，其余改成链接；能把这句话钉成 `forbidden-regex` 的就钉住。
- **越界的历史**：当前态文档里出现「以前是」「这版改掉了」→ 移进决策记录。
- **实现状态标注**：「已实现」「将来会……」。状态会腐烂，仓库布局与清单才是状态。
- **手抄目录**：源码或生成器是权威时，别在文档里再抄一份工具、事件、包或检查的清单。
- **思维过程泄漏**：逐步实现叙述、明显分支的证明、测试走查、被否的局部方案。留结论，删路径。
- **段落墙**：一段塞进几条规则和插入语。拆开，或把细节降到它的家。
- **强调通胀**：到处都是加粗与「关键」等于没有重点。
- **正文复述代码**：注释与文档重述控制流，而不是写下契约、失败模式、时序和所有权。

## 已知边界

- `budget` 的 `maxWords` 与 `wc -w` 同口径，词间没有空格的文本会被严重低估；只有空白分隔的正文才适合按词设顶。所以本套件的中文页用的是行上限 —— 活的例子就是这一对。
- `publish-manifest` 只保证三件事：每个文件恰好归类一次、`public` 逐条点名真实存在的文件、`internal` 给出理由。它判不了这些 `public` 文件是否真被站点构建收走；那一半属于你放在 `docs` 面上的构建命令。
- **生成页的新鲜度不由 `check-invariants` 判定**：它不跑任何生成器。`--check` 是一条由 `run-evidence` 执行的证据，在它跑之前，没有人证明过页面是当前投影。
- `source-mirror` 比对的是**文本区间，不是 AST**：它能抓到一次没有跟随源的粘贴，抓不到两处写法不同、含义相同的声明。按语言做 AST 比对是你自己的活。
- `source-mirror` 只把清单里列出的围栏 info string 当作镜像，所以 info string 拼错的块（`text miror`）会**静默地**变成普通示例。把镜像块统一放在 `dev/contracts/` 下、用固定 info string，或者在评审里逐条核对。
