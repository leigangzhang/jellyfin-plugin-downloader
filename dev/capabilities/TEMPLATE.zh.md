# Capability: `<key>`

[English](TEMPLATE.md) | 中文

> 这份文件是 [registry.json](registry.json) 里**一条条目**的形状，它本身不是条目。能力（capability）是一种**换掉实现而消费者不需要改动**的东西；它有**三个角色**，一个文件或包只有在这些角色确实是同一件关注点时，才可以同时装下它们。

## 条目

```json
{
  "key": "<stable-name>",
  "kind": "seam",
  "definition": "<path that owns the interface>",
  "providers": ["<path that implements it>"],
  "consumers": ["<path that uses it>"],
  "note": "<why these roles exist, and why they do or do not split>"
}
```

## 字段

| 字段 | 规则 |
|---|---|
| `key` | 稳定名称，在登记表里唯一。它必须原样出现在拥有该能力的源码里，跟在行首标记之后 —— 默认是 `# capability: <key>`。 |
| `kind` | `seam` / `core` / `service` / `bundle` 之一。只有 `seam` 主张那三个角色。 |
| `definition` | 拥有接口的那**一个**路径。它必须存在。 |
| `providers` | 实现该接口的路径。`seam` 至少要有一个。 |
| `consumers` | **现在就在用**该接口的路径。`seam` 至少要有一个。 |
| `note` | 这些角色为什么长这样。非 `seam` 的种类必须在这里说明它**为什么不是** seam。 |

## 三个角色

- **Definition** —— 接口与词汇。它可以是一个抽象声明、一个登记表，或一个其它代码在其背后替换的具体类；它绝不是"第一个实现碰巧导出的那些东西"。
- **Provider** —— 针对该 Definition 注册的一个实现。出现第二个 Provider，通常就是把角色拆开的原因。
- **Consumer** —— 面向 Definition 编程、绝不面向某个 Provider 类型的代码。

**一个角色不构成能力。** 没有 Provider 的 Definition 是愿望；没有 Consumer 的 Provider 是负债；耦合在一个 Provider 上的 Consumer 不可替换。

## 拆分规则

1. **不要预先拆分。** 只有一个可想象的 Provider 和一个 Consumer 时，它们就待在一个文件或包里，直到第二个出现。
2. **必须有当前消费者。** 没有当前消费者的抽象、开关或兼容路径，是拒绝而不是搁置。
3. **按变化速率拆分。** 只有角色因不同原因变化时才分开 —— 替换一个 Provider 不该逼得 Consumer 的契约跟着动。
4. **例外也要写理由。** `core` / `service` / `bundle` 条目不是失败；把理由留空才是。写在 `note` 里。

## 让登记表保持诚实

登记表是手写的；存在性不是。每个能力都在自己拥有的源码里带一行行首声明 —— 默认 `# capability: <key>`，标记字符串按项目配置 —— 而 `capability-registry` 检查（check）要求**声明的键与登记的键完全相等**：

- 有声明没条目 → 未分类的能力；
- 有条目没声明 → 陈旧的登记行。

声明必须是本行第一个非空白文本，所以一条只是提到该标记的注释不会登记任何东西。

适用于每条条目的规则：键唯一；`definition` 存在；每个 provider 与 consumer 路径都存在；`seam` 每种角色至少一个。新增一个能力，就是在同一次改动里加上标记、登记条目和任何相关记录（record）。
