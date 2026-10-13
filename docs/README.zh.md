# 文档索引

[English](README.md) | 中文

本套件的文档分别是什么、各自回答什么。本页只负责路由：规则与事实住在它链接的那些家里，这里不复述任何一条。本页也是**唯一**列出全部家及其归属的地方。

| 你的问题 | 去哪 |
|---|---|
| 这到底是什么，怎么装进我的仓库？ | [kit.md](kit.md) |
| 十一站各自产出什么，由哪道闸门判定？ | [dev/README.md](../dev/README.zh.md) |
| 破坏性改动怎么给读者留迁移路径？旧记录怎么退役或封印？ | [evolution.md](evolution.md) |
| 这份文档该怎么写、住在哪、给多少预算、能不能发布？ | [documentation.md](documentation.md) |
| 这些文档是双语的，两侧怎么保持同步？ | [i18n.md](i18n.md) |
| 这个仓库怎么判定某件事"已验证"？ | [testing.md](testing.md) |
| 我要扩展套件本身 —— 加一个改动面、一条检查、一个技能、一条预算 —— 怎么做？ | [extending.md](extending.md) |
| 我要把这套东西采用进一个已经存在的仓库 —— 有什么不一样？ | [adopting.md](adopting.md) |
| 常驻规则是什么？ | [AGENTS.md](../AGENTS.md) |
| 五个脚本各自做什么，在哪里停下来？ | [tools/README.md](../tools/README.zh.md) |
| 此刻某道闸门在查什么？ | [check-catalog.md](check-catalog.md)（生成页，永不手改） |
| 哪些文档会发布，哪些留在仓库里？ | [publish.json](publish.json) |
| 某个决定当时为什么这么做，放弃了什么？ | [.agents/notes/README.md](../.agents/notes/README.zh.md) |
| 写文档时我要遵守哪些规则？ | [.agents/skills/write-docs/SKILL.md](../.agents/skills/write-docs/SKILL.md) |
| 这个词在这里什么意思，我该用哪种写法？ | [glossary.md](glossary.md) |
| 怎么把某个术语讲给从没读过这套件的人？ | [plain-language.md](plain-language.md) |

## 每个家，以及它拥有什么

| 家 | Kind | 档位 | 闸门 | 它拥有 |
|---|---|---|---|---|
| [kit.md](kit.md) | guide | 全档 | — | 套件是什么、三个采用分档、接下来去哪 |
| [AGENTS.md](../AGENTS.md) | standing rules | 全档 | `context-budget` | agent 在每个上下文都要遵守的规则，以及唯一的命令清单（`context-budget` 把它压在 120 行内） |
| [dev/README.md](../dev/README.zh.md) | guide | 全档 | — | 生命周期地图：十一站的进出口、它的闸门与反模式 |
| `docs/` | policy | 全档 | `docs-policy`、`i18n-policy`、`evolution-policy`、`testing-policy`，各带一条预算 | 套件的常驻规则：文档、词汇、语言、演化、测试 |
| [tools/](../tools/README.zh.md) | machinery | 全档 | —（它实现全部闸门） | 五个脚本、它们唯一的配置（`workflow.json`），以及改它们的规矩 |
| [.agents/skills/](../.agents/skills/README.md) | skills | 全档 | `skill-record`、`skill-trigger` | 触发层，由 `description` 加载，而不是按顺序通读 |
| [.agents/notes/](../.agents/notes/README.zh.md) | records | 全档 | `decision-*`、`no-proposal-era-headings`、`criteria-traced`、`note-class`、`notes-readme` | 决策记录：提案、已实现的决定、被否决的提案，每份都归在类目目录下 |
| [dev/contracts/](../dev/contracts/TEMPLATE.md) | contract | 最小档 | `contract-record`、`contract-mirror`、`contract-kinds-budget` | 接口、镜像声明，以及契约的 kind 清单 |
| [dev/capabilities/](../dev/capabilities/TEMPLATE.md) | records | 最小档 | `capability-record`、`capability-registry` | 能力登记处：seam、core、service、bundle |
| [dev/review/](../dev/review/TEMPLATE.md) | records | 最小档 | `review-record` | 评审记录：只有读代码才能得出的发现 |
| [dev/release/](../dev/release/TEMPLATE.md) | records | 交付档 | `release-record` | 发布记录：版本、范围、证据、破坏性变更、回滚 |
| [dev/upgrade-guide/](../dev/upgrade-guide/TEMPLATE.md) | records | 交付档 | `dev/upgrade-guide`、`upgrade-guide-budget` | 每个被破坏的面一份迁移指南，写在破坏它的那次改动里 |
| [dev/postmortem/](../dev/postmortem/TEMPLATE.md) | records | 长期档 | `postmortem-record`、`postmortem-guardrail` | 事故复盘：流程为什么会放它过去 |
| [.agents/notes/archived/](../.agents/notes/archived/manifest.json) | archived | 长期档 | `archive-seal` | 被封印的记录，连同摘要与归档日期一起冻结 |
| [tests/](../tests/README.zh.md) | tests | 全档 | `no-time-based-test-sync` | 套件自己的契约测试，由 [testing.md](testing.md) 里的 Unit 命令运行 |
| `dev/evidence/` | generated | 生成 | — | `run-evidence.py` 写下的东西：每次运行、每条命令一个判定 |


「档位」列对应 [kit.md](kit.md) 里的三个拷贝集合：`全档`是三档都带，`生成`在第一次跑 `run-evidence.py` 时出现，`仅本套件`永不随拷贝出去。每个家同时受通用闸门覆盖 —— `publish-manifest`、`no-secrets`、`no-conflict-markers`、`banned-spellings` —— 所以「闸门」列只列该家专属的那几条。