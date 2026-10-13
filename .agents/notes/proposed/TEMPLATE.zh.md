# Proposal: <一行标题>

[English](TEMPLATE.md) | 中文

Status: proposed
Date: <yyyy-mm-dd, the day the topic was first proposed>
Class: feature | bug-fix | simplification | architecture | process | testing

## 问题

<动机，写成没有方案也能独立成立的样子。什么坏了、坏了谁的事、今天要付出什么代价。写事实与观察，不写形容词。>

## 提案

<打算做的改动，用将来时写。计划、迁移步骤和待定问题在工作还没落地时都属于这里。点名它将新增或改动哪些确切的文件、键、接口或命令。>

## 曾考虑的替代方案

<必填，且不得为空。每个真实备选方案一段、以加粗开头，并写明它为什么落选。只记录发生过的，绝不编造：如果一个方案真的从未上过桌，就明说，并说明为什么没上桌。>

**<备选方案 A>。** <它为什么落选。>

**<备选方案 B>。** <它为什么落选。>

## 验收标准

<什么可观测状态算做完。每一行都必须能翻译成一条断言、一条命令或一份快照 —— 如果你想象不出那个检查，它就是愿望，不是验收标准。给每一行一个稳定编号，并在反引号里点名它会变红的检查或改动面，这样 dev/README.md 里的最后一个问题（「如果我现在把它改坏，哪条检查会失败？」）就写在结论旁边。下面两条追溯 `tools/workflow.json` 里真实存在的检查；把它们替换掉。>

- [A1] `decision-proposed` rejects a proposal whose `## Alternatives considered` body is empty.
- [A2] `criteria-traced` rejects a criterion that names no declared check or surface.

## 风险

<什么可能出错，以及这次改动有意放弃了什么。写上这个决策该在什么条件下重新审视。>

## 交付阶段

<可选。当改动大到无法一次落地时，把它拆成有序的阶段，每一段都能独立发布、独立测试。小改动删掉这一节。>

1. <stage> — <what it makes true>
2. <stage> — <what it makes true>
