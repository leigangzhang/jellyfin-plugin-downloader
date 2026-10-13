# 套件的脚本

[English](README.md) | 中文

`tools/` 下五个零依赖的 Python 3 脚本就是本套件的全部实现，其余全是 Markdown 与 JSON。[workflow.json](workflow.json) 是它们唯一的配置，改它们的规矩住在 [AGENTS.md](AGENTS.md)。每个脚本的 module docstring 就是它的完整参考 —— `python3 tools/<script> --help` 会打印出来 —— 所以本页只讲三件事：每个脚本回答什么问题、结果怎么读、它在哪里停下来。

## 目录结构
| Path | What it is |
|---|---|
| `check-invariants.py` | 约定运行器（薄入口） |
| `kitcheck/` | `check-invariants.py` 背后的引擎包；下一节列出它的模块 |
| `gen-docs.py` | 检查目录生成器 |
| `pair-docs.py` | 配对记录器与校验器 |
| `run-evidence.py` | 证据运行器 |
| `change-scope.py` | 改动面映射器 |
| `workflow.json` | 唯一的配置：改动面、检查、证据命令与配对范围 |
| `tiers.json` | 唯一的档位开关：本项目跑哪些 stage、各自在哪一档 |
| `AGENTS.md` | 改本页所述任何东西的规矩 |
| `README.md` | 本页，也是五个脚本的参考 |

### kitcheck模块

`check-invariants.py` 背后的包，一个主题一个模块。新守卫放进它主题所属的模块，新主题就开新模块（[规矩](AGENTS.md)）。

| 模块 | 它拥有什么 |
|---|---|
| `core.py` | 所有守卫共享的东西：配置、文件遍历、行数与词数、以及 kind 常量 |
| `policies.py` | 文档形状的守卫：预算、必需小节、禁用正则 |
| `records.py` | 类目守卫：记录的目录、它的 `Class:` 行与闭集三者必须一致 |
| `publication.py` | 发布守卫：每份文档恰好归类一次，且 `public` 点名的都是真实文件 |
| `seals.py` | 封存守卫：被封存的记录被冻结，且封印与记录必须写明同一日期 |
| `criteria.py` | 准则守卫：每条准则都点到已声明的检查或改动面，并能活着搬进 `## Testing` |
| `mirrors.py` | 镜像守卫：粘贴块绑定到它的源码区间 |
| `capabilities.py` | 能力守卫：登记表与源码里的标记必须一致 |
| `skills.py` | 技能守卫：description 是触发条件，不是内容摘要 |
| `tiers.py` | 档位开关：声明的档位，以及证明树与之一致的检查 |
| `registry.py` | kind、运行器，以及证明每一条都能失败的自检 |
| `cli.py` | 命令行 |
| `__init__.py` | 包门面：其余模块与测试都从这里导入 |

## 五个脚本

| 脚本 | 它回答什么 | 标准调用 |
|---|---|---|
| `change-scope.py` | 改了什么、命中哪些改动面、每个面要什么证据 | `python3 tools/change-scope.py --base <ref>` |
| `run-evidence.py` | 真的去跑那些证据，并给每条命令记一个判定 | `python3 tools/run-evidence.py --base <ref>` |
| `check-invariants.py` | 此刻哪些可执行约定成立，以及每道守卫是否真能失败；`--changed <ref>` 只跑 patterns 命中这次改动的那些检查（迭代模式），整套则是边界那一跑 | `python3 tools/check-invariants.py --self-test` · `python3 tools/check-invariants.py --changed <ref>` |
| `gen-docs.py` | 已提交的[检查目录](../docs/check-catalog.md)是否仍是当前投影 | `python3 tools/gen-docs.py --check` |
| `pair-docs.py` | 每一对双语文档是否齐全且同步 | `python3 tools/pair-docs.py --check` |

`change-scope.py` 只读，而且**从不猜 base**：它打印改动路径、每个改动面要求的证据，以及每一条没有归属的路径。`--strict` 把"没人声明过的新文件"变成失败，这条正是它能接进 CI 的原因。其余四个都带 `--check` 模式，而那些模式就是 [workflow.json](workflow.json) 里各改动面上声明的证据。

## 结果怎么读

`run-evidence.py` 给每条命令三选一的判定，让"改动是坏的"和"工具是坏的"不再混成一个信号：

| 判定 | 判据 | 计入通过 |
|---|---|---|
| `PASS` | 解析成功且退出 0 | 是 |
| `FAIL` | 解析成功、跑起来了、退出非 0 —— 这是对改动的判定 | 否 |
| `UNKNOWN` | 根本没能跑起来：执行时解析不到、shell 报 126/127、进程起不来 | **否** |

无法判定**绝不等于通过**。手工条目单独列成 "you must still do these"，**永不计入验证过**；已存在的记录也不会被覆盖：同一天同一分支再跑，会在旁边写一个带编号的后继。[testing.md](../docs/testing.md) owns 决定"该有哪些命令"的分层策略。

## 已知边界

这五个脚本 —— 以及 `check-invariants.py` 跑的那些检查 —— 只保证以下写明的部分，一条都不多。每一条都是有意为之。

- `change-scope.py` 用 `git status --porcelain --untracked-files=all` 读工作区，而 git 会给含特殊字符的路径加引号；落在被忽略目录里的路径根本不会出现。要机器接口就用 `--json`。
- `run-evidence.py` 只能看到 runner 那一层：解释器起来了、但它被要求打开的脚本已经不在了时，退出码是解释器自己的，与一次真实的断言失败无法区分，于是被记成 `FAIL`。保守方向是对的 —— 多记一次 `FAIL` 只是白跑一趟，漏记成 `PASS` 才是灾难。
- `run-evidence.py` 从不覆盖已有记录：同一天同一分支再跑会写出 `-2`、`-3` 后继。手工改过的旧记录同样没有守卫；让生成物冻结下来靠的是历史与评审。
- `check-invariants.py` 覆盖的是**路径形状**的事实：计数、正则、摘要与登记集合。它判不了语义不变量；那需要一条项目自己的测试，声明成那个面的证据。
- `criteria-traced` 只保证每条验收标准带编号、并且点名了一条已存在的检查或改动面。它判不了这条标准是否真的可判定、点名的归属对不对，也不检查编号唯一性 —— 一条格式漂亮的愿望照样通过。
- `capability-registry` 靠**标记**发现存在，而不是解析语言，所以 `discover.patterns` 必须窄到只覆盖真正拥有能力的源文件；文档或测试夹具会造出假声明。它的 kind 集合是封闭的，新增一种就是 [AGENTS.md](AGENTS.md) 里那处四处齐改。
- `skill-record` 判不了内容质量，也判不了 `description` 里的触发语写得好不好：技能是给 agent 读的，本套件从不执行它们。
- 以上这一切的代价是**维护检查本身**。低于某个规模时，检查的成本会超过它带来的回报 —— 这就是本套件给的是分档而不是一刀切的原因。
