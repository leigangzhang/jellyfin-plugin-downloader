# 最小可用 Agent 工作流套件

[English](kit.md) | 中文

一套可以直接拷进任何仓库的模板，从 DeepSeek Harness 提炼出**十一站生命周期**的最小实现：

1. **给常驻上下文定预算** —— 根规则文件保持短小，每条规则恰好只有一个家。
2. **每个决定都留下痕迹** —— 提案必须写下它否掉了什么，决定必须写下它付出了什么代价。
3. **把约定变成能变红的检查** —— 没人能写成脚本的规则等于不存在。
4. **把事故变成检查** —— 留下"当时什么是绿的"，外加一道你亲眼见它红过的闸门。
5. **给生命周期一副骨架** —— 每一站都要产出**可判定**的东西，判不了的东西不许进入下一站。
6. **让技能决定何时出手** —— 模板回答"它长什么样"，闸门回答"证明它做对了"；中间缺的那一环是：什么时候该读哪一份。
7. **先设计，再实现，然后才叫能力** —— 接口与验收标准住在提案里，而它是否够格拥有可替换的三个角色，由登记处回答。
8. **让每条验收标准点名它会为哪条检查变红** —— 验收标准在记录移档时保住自己的编号；追不到检查的那条是愿望，不是标准。
9. **让测试策略只有一个家** —— 分层、改动面→证据、确定性、负控制与 flake 策略都住在 [testing.md](testing.zh.md)，而每条规则何时适用由 `engineer-tests` 技能决定。
10. **默认不信任绿色信号** —— 每一个"通过"都要说明它凭什么算证据；不信任只花在**跨边界**、且信号可能说谎的地方（[技能](../.agents/skills/apply-distrust/SKILL.md)）。
11. **一个事实一个家** —— 文档先拿到 kind，kind 决定它的骨架、预算与读者；一份人和 agent 都要照着做的文档会变成**一对**：英文 canonical 文件、中文对侧，以及一份一致性记录（[documentation.md](documentation.zh.md)、[i18n.md](i18n.zh.md)）。
12. **让旧东西真的消失** —— 破坏性改动在**引入它的那次改动里**写下迁移路径；已被消费的产物只追加，退役的记录被封印并冻结（[evolution.md](evolution.zh.md)）。

它不假设任何语言、框架或工具链：唯一的实现是 `tools/` 下**五个零依赖的 Python 3 脚本**，其余全是 Markdown 与 JSON。

> **先读[生命周期地图](../dev/README.zh.md)** —— 十一站的完整陈述、每站的进出口、它的闸门与反模式都在那里。本文件只讲怎么把套件装进你的仓库，以及接下来去哪。

---

## 30 秒上手

按你的分档拷贝文件；三档的区别见[地图](../dev/README.zh.md)。这条路假定仓库是空的 —— 如果里面已经有代码、文档和消费方，[次序会不一样](adopting.zh.md)。

最小档：一个人快速迭代，还没有用户 —— 发布、演化、存档、否决记录与事故复盘都先不要。

```sh
cp -r AGENTS.md CLAUDE.md .agents docs dev tools tests <your-repo>/
rm -rf <your-repo>/dev/{release,upgrade-guide,postmortem} <your-repo>/.agents/notes/{archived,rejected}
```

交付档：已经有公开接口，别人在用了 —— 加上发布与演化。

```sh
cp -r AGENTS.md CLAUDE.md .agents docs dev tools tests <your-repo>/
rm -rf <your-repo>/dev/postmortem <your-repo>/.agents/notes/{archived,rejected}
```

长期档：全都要 —— 存档、否决记录与事故复盘在这里加入。

```sh
cp -r AGENTS.md CLAUDE.md .agents docs dev tools tests <your-repo>/
```

凡是不在 [i18n.md](i18n.zh.md#范围与排除) 排除表里的文档都成对发货：上面的拷贝命令带走的每一份 `dev/README.md` 都同时带来 `dev/README.zh.md` 与 `README.i18n.yaml`。非要只留一种语言时，就删掉另一侧，并在**同一次改动里**注释掉 `i18n` 面那条检查 —— 空语料是红的，而一条没人能满足的检查比没有检查更糟。 `CLAUDE.md` 是指向本套件入口页的指针，给读这个文件名的宿主用；你的宿主不读它就删掉。

> **本套件自己不带任何记录。** `.agents/notes/` 里，每个生命周期目录的 `TEMPLATE.md` 就是你要拷的骨架；`dev/evidence/` 下唯一一个文件就是 `TEMPLATE.md`。从更早的版本拷过来的副本里可能还留着那一版的决策历史与运行记录 —— 那些不是你的项目的，删掉它们：

```sh
find .agents/notes -mindepth 3 -type f \( -name '*.md' -o -name '*.zh.md' -o -name '*.i18n.yaml' \) ! -name 'TEMPLATE.*' -delete
find dev/evidence -mindepth 1 -type f ! -name 'TEMPLATE.md' -delete
```

> 删完之后每个目录仍留着自己的 `TEMPLATE.md`，所以那几条决策检查不会因为空语料变红。**没装的每一个 stage，都请在 [tools/tiers.json](../tools/tiers.json) 里声明为 `none`** —— 开关会把它变成声明的缺席，只拥有它那个家的检查不再运行（阶梯见[地图](../dev/README.zh.md)）。文件还留着就是红，这正是要点：开关陈述意图，树陈述事实。

然后只改一个文件：把你项目真实的路径与命令填进 [tools/workflow.json](../tools/workflow.json) —— 改动面的 patterns、每条证据命令、以及各条检查的 patterns —— 再用 `python3 tools/run-evidence.py --check` 自检这份配置。最后跑一遍 [README.md](../README.md#常用命令) 里列出的命令，确认它们在你的仓库里真的能跑。

> `.agents/skills/` 装了十四个技能，用不上的删掉。但**不要清空 `.agents/skills/`**：`skill-record` 会匹配不到任何文件，从而踩空语料规则。记得把你宿主的技能加载器指向 `.agents/skills/`：`description` 只是一个匹配条件，没人加载的目录就是一篇没人打开的文章。`tests/` 装的是本套件**自己的**契约测试（只用标准库 `unittest`）：它既是 [testing.md](testing.zh.md) 里的 Unit 命令，也是"这套设计真的有一条能跑起来的命令"的证明。

---

## 去哪找什么

| 你的问题 | 去哪 |
|---|---|
| 这到底是什么，怎么装？ | 本文件 |
| 十一站各自产出什么，由哪道闸门判定？ | [地图](../dev/README.zh.md) |
| 一份文档该怎么写、给多少预算、能不能发布？ | [documentation.md](documentation.zh.md) |
| 这些文档是双语的，两侧怎么保持同步？ | [i18n.md](i18n.zh.md) |
| 破坏性改动怎么留迁移路径？记录怎么退役或封印？ | [evolution.md](evolution.zh.md) |
| 这个仓库怎么判定某件事"已验证"？ | [testing.md](testing.zh.md) |
| 常驻规则是什么？ | [AGENTS.md](../AGENTS.md) |
| 五个脚本各自做什么，在哪里停下来？ | [tools/README.md](../tools/README.zh.md) |
| 我要扩展套件本身，怎么做？ | [extending.md](extending.zh.md) |
| 我要把它装进一个已经存在的仓库，有什么不一样？ | [adopting.md](adopting.zh.md) |
| 此刻某道闸门在查什么？ | [check-catalog.md](check-catalog.md) |
| 哪些文档会发布，哪些留在仓库里？ | [publish.json](publish.json) |
| 这个词在这里什么意思？ | [glossary.md](glossary.zh.md) |
| 怎么把某个术语讲给没读过这套件的人？ | [plain-language.md](plain-language.zh.md) |
| 某个决定当时为什么这么做，放弃了什么？ | [.agents/notes/README.md](../.agents/notes/README.zh.md) |

本仓库里的每一个家、以及各自拥有什么，只在 [docs/README.md](README.zh.md) 里索引一次。

## 刻意不做的事

七件本套件有意拒绝的事 —— 不给技能或指令文件做配对、不做 CHANGELOG、不给覆盖率数字、不做审批权重、不带内置 CI、不"每次改动都跑全栈"、不评判内容质量 —— 每一条的理由住在它主题所属的地方，由 [docs/README.zh.md](README.zh.md) 索引。
