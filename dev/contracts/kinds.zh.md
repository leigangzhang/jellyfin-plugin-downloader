# Contract: 检查种类

[English](kinds.md) | 中文

## 接口

`tools/check-invariants.py` 只接受 `SUPPORTED_KINDS` 里列出的那些检查（check）种类。配置里点名别的种类是环境错误（退出码 2）；声明了某个种类、但它的参数不合法，同样是环境错误。每条声明的检查必须至少匹配一个文件：没有检查对象的检查会被拒绝，永远不会判通过。

## 事实源

`tools/check-invariants.py` 拥有这份清单，就在它的 `# region: supported-kinds` 与 `# endregion: supported-kinds` 标记之间。新增一种 kind 是**四处齐改** —— runner、`SUPPORTED_KINDS`、一条自带负控制探针的自测用例，以及本文档里的种类清单 —— 四处没齐之前，下面的粘贴一直是红的。

## 投影

下面的粘贴是完整的 `SUPPORTED_KINDS` 元组，用 `text mirror` 围栏。它是投影，不是第二份定义：`dev/contracts/mirrors.json` 登记它，`python3 tools/check-invariants.py` 把它与源码里被标记的区间比对，只忽略行尾空白与首尾空行，别的一概不忽略。

## 检查

```sh
python3 tools/check-invariants.py             # fails on a stale paste
python3 tools/check-invariants.py --self-test # proves each kind can go red and green
```

## 漂移政策

在同一次改动里更新粘贴与元组；块或任一标记被改名时更新 `dev/contracts/mirrors.json`；检查种类退役时删掉这份记录（record）。镜像所在的文档掉出该检查的 `patterns` 会被报出来，所以重新划定检查范围不可能悄悄让这份粘贴变成孤儿。

## 登记的粘贴

```text mirror
SUPPORTED_KINDS = ("tier-manifest", "budget", "required-sections", "source-mirror", "capability-registry", "criteria-traced", "forbidden-regex", "note-class", "link-target", "evidence-record", "skill-trigger", "publish-manifest", "sealed-manifest")
```
