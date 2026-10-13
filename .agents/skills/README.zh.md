# 技能

[English](README.md) | 中文

这里的每个目录是一个**触发层**：一份 `SKILL.md`，agent 只在它自己的条件命中时才加载。这一页是一条入口 —— 该找哪一个 —— 并且**有意不去列出**每个技能何时加载：那是每个技能自己的 `description`，也正是加载器读的那段文字。

## 怎么用

`description` 命中当前工作时，agent 会按名字加载某个技能；人则输入 `/name`，把同一份指令注入当步。技能是被**加载**的，不是按顺序通读的，所以下面那张表是一条入口，不是一个次序。每个技能都带同样四节 —— `## When to use`、`## How`、`## Verification`、`## Anti-patterns` —— 而且一份文件同时服务两类读者，所以技能从不翻译（[i18n.md](../../docs/i18n.zh.md#范围与排除)）。

## 该找哪一个

| 你手上的问题 | 技能 | 它给你什么 |
|---|---|---|
| 这请求是一个目标，不是一次改动 | [`shape-intent`](shape-intent/SKILL.md) | 目标会变成哪几份封闭的提案，以及它们的次序 |
| "做完"是什么意思？ | [`define-acceptance`](define-acceptance/SKILL.md) | 一条检查能为其变红的验收标准 |
| 把这次会话定下来的东西冻结 | [`record-decision`](record-decision/SKILL.md) | 已定决策写进记录，它们原先所在的那个文件随之删掉 |
| 这次改动需要哪些检查？ | [`pick-evidence`](pick-evidence/SKILL.md) | 覆盖这次改动的最小证据，以及它们的执行次序 |
| 这个测试到底有没有用？ | [`engineer-tests`](engineer-tests/SKILL.md) | 这个用例属于哪一档，以及它欠下的确定性 |
| 我可以说这已经验证过了吗？ | [`apply-distrust`](apply-distrust/SKILL.md) | 把"外部观测"与"自述"分开的那些问题 |
| 这次改动能交付了吗？ | [`review-standard`](review-standard/SKILL.md) | 一次评审必须找出什么，以及它的记录必须承载什么 |
| 怎么把它落地？ | [`land-changes`](land-changes/SKILL.md) | 落地前核对、依赖次序，以及落地后的确认 |
| 已经有东西消费过它，而现在坏了 | [`migrate-and-retire`](migrate-and-retire/SKILL.md) | 后继版本、升级指南，以及封印 |
| 发布出去 | [`cut-release`](cut-release/SKILL.md) | 从仓库之外取得的发布证据 |
| 写或重构文档 | [`write-docs`](write-docs/SKILL.md) | 文档种类、摆放、预算，以及发布开关 |
| 写给一个人看 | [`plain-language`](plain-language/SKILL.md) | 什么术语必须展开，以及怎么展开 |
| 这段文字读起来像思维过程泄漏 | [`trim-prose`](trim-prose/SKILL.md) | 该删什么，以及删完之后什么必须留下 |
| 让它更快 | [`measure-perf`](measure-perf/SKILL.md) | 改动之前先测量，改动之后有一道闸门 |

## 相关

- 本仓库的每一个家、各自拥有什么：[docs/README.zh.md](../../docs/README.zh.md)。
- 一个技能必须带什么、凭什么被加载：`skill-record` 与 `skill-trigger` 两条闸门，声明在 [tools/workflow.json](../../tools/workflow.json)。
