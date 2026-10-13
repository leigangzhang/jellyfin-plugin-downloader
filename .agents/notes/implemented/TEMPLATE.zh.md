# Decision: <一行标题>

[English](TEMPLATE.md) | 中文

Status: implemented
Date: <yyyy-mm-dd, the day the topic was first proposed>
Class: feature | bug-fix | simplification | architecture | process | testing

## 问题

<动机，从提案里原样保留。它必须在没有方案的情况下依然成立。>

## 决策

<现在成立的事实，用现在时写。点名承载这个决策的确切文件、键、默认值、事件或命令，让读者能拿每一句话去对代码。整份记录跟实际发布的东西保持同步：路径、符号或默认值一变，就在这里更新。>

## 曾考虑的替代方案

<必填，且不得为空。从提案里保留，并补上此后学到的东西。每个真实备选方案一段、以加粗开头，并写明它为什么落选。>

**<备选方案 A>。** <它为什么落选。>

**<备选方案 B>。** <它为什么落选。>

## 后果

<这个决策换来了什么、代价是什么。提案里的验收标准与风险在这里折成现在时的事实：什么现在不可能、不被支持或有意不做，以及这个决策该在什么条件下重新审视。>

## 验证

<现在用什么证明这个行为，按确切文件或命令写，用现在时。保留提案里的编号，并把每一条放在它会变红的检查或改动面旁边；丢掉了编号的标准就丢了它的证明，`criteria-traced` 会拒绝它。如果没有任何东西钉住某条标准，就写 "unpinned" 并说明为什么这可以接受。下面两条追溯 `tools/workflow.json` 里真实存在的检查；把它们替换掉。>

- [A1] `no-proposal-era-headings` rejects an implemented record that still carries `## Acceptance criteria`.
- [A2] `skill-record` rejects a skill missing one of its four required sections.

---

<!-- 把一份记录从 proposed/ 移到 implemented/ 不是一次改名。
     骨架变了：## Proposal 变成 ## Decision，
     ## Acceptance criteria 与 ## Risks 折叠进 ## Consequences。
     仍带着提案期标题的记录通不过 decision-implemented。
     事实可以原地改；决定本身不可以被改写。
     推翻一个决定，就写一份新记录，并让两者交叉链接。 -->
