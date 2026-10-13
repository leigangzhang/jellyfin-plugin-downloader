# 术语表

[English](glossary.md) | 中文

一个概念一个词。本表对**新写的文字**有约束力：它说明每个词在这里是什么意思、该用哪种写法、以及本套件与宿主仓库不一致时宿主管它叫什么。译法住在 [i18n.md](i18n.zh.md#术语)，而"怎么把术语讲给不共享这套词汇的人"住在 [plain-language.md](plain-language.zh.md)。

## 如何使用本术语表

- 给新概念、新检查、新文档 kind 起名之前先读它：这里已经有意思的词，不能拿去表示别的东西。
- 用锚点引用术语 —— `glossary.md#check` —— 而不是复述它的定义。
- 每个词条带三样东西：它是什么、一句话可能被读成两种意思时该写哪种限定形式、以及两套词汇不一致时宿主的叫法。

## 如何新增一个术语

一个词够格进来，当且仅当：第二份文档需要用同一个概念、两个词在争同一个意思、或者一个词有两个意思 —— 后者应当拆成两个带限定的词条，而不是一条含糊的。除此之外都不属于这里：本表不是译法表（那是 [i18n.md](i18n.zh.md#术语)），也不是宿主词汇的搬运场。

## 结构

### station

生命周期上的一站，从意图到退役（[dev/README.md](../dev/README.zh.md)）。写 `station`；`stage` 在这里不是同义词；带编号的站写作 `station ⑥`。宿主叫法：宿主没有站，只有包与子系统。

### cross-cutting station

不是序列中一步的站：⚡ 事故与 📄 文档，只要它们的对象出现就适用。

### tier

三个采用分档之一 —— 最小档、交付档、长期档 —— 决定这份套件的副本包含哪些家与哪些检查。分档不是质量等级。

### home

拥有某个事实的那一个文件或目录。"一个事实一个家"是规矩；其余提到它的地方都链向那里。

### document kind

一份文档扮演的角色 —— `guide`、`index`、`generated`、`contract`、`record`、`skill`、`template`、`reference` —— 它决定骨架、预算与读者（[documentation.md](documentation.zh.md)）。裸用 `kind` 有歧义：写 `document kind` 或 `check kind`。

## 检查与证据

### check

一条已声明的可执行约定 —— `id`、`kind`、`patterns` 与该 kind 的旋钮 —— 声明在 [tools/workflow.json](../tools/workflow.json)、由 [tools/check-invariants.py](../tools/check-invariants.py) 运行。点名一条检查就写它的 id；永远不要叫它"那道闸门"。

### gate

集合名词，指守住某一站出口的那组检查 —— "评审闸门"、"发布闸门"。它从不指代某个可执行的东西：那就写 `check` 加它的 id。宿主仓库干脆禁止这种隐喻；本套件只在上述集合意义下保留这个词。

### patterns

一条检查用来决定读哪些文件的 glob 列表。patterns 匹配不到任何文件的检查判**无效**，而不是判过。

### surface

[tools/workflow.json](../tools/workflow.json) 里 `surfaces` 列表的一项：一组具名路径，加上这组路径要求的证据命令。

### changed surface

一次改动**实际**碰到的文件集合，由 [tools/change-scope.py](../tools/change-scope.py) 报出。与 `surface` 不同：后者是声明的，不是观测到的。

### evidence

两个都现行的意思：改动面上声明的**证据命令**（由 `run-evidence.py` 执行），以及 `dev/evidence/` 下的**证据记录**（每条命令一个判定）。一句可能指两者的句子，必须写限定形式。

### budget

一个文件的额度 —— `maxLines`、`maxWords`，或两者。空白分隔的正文按词，中文正文按行。

### lane

一条检查如何参与结论：**blocking** 车道能让改动失败，**observational** 车道只报告不拦（[testing.md](testing.zh.md)）。

### guard

为抓某一个特定回归而存在、并且被亲眼看着为该回归红过一次的检查。不是每条检查都是守卫。

### empty corpus

一条检查的 patterns 匹配不到任何文件的状态。它算违规，绝不算通过：没有主语的检查无法失败。

### self-test

`check-invariants.py --self-test`：证明每个检查 kind 都拒掉违规夹具、接受合规夹具的那一次运行。

## 记录与退役

### decision record

`.agents/notes/<lifecycle>/<class>/<yyyy-mm-dd>-<module>-<slug>.md` 下的记录，留下一个决定的问题、选择、被否方案、后果与验证（[.agents/notes/README.md](../.agents/notes/README.zh.md)）。写 `decision record`；单独的 `note` 不是同义词。只有当它的 `## Decision` 能写成一条[约束句](#standing-constraint)、且别处没有这条约束时才写。宿主叫法：**Agent Note**。

### standing constraint

一条对现在成立、后续改动可以拿它对照的句子 —— "X 归 Y 所有"、"Z 永远不会被写到线上"。只能写成一次已完成动作（"我们把 X 搬了"）的 `## Decision` 不是约束句，不写记录。

### session decisions file

一个会话定下来的[约束句](#standing-constraint)所在的工作文件，路径 `.agents/notes/.session-decisions/<session>.md`；每条自足，压缩过的会话能只靠它继续。`.gitignore` 忽略整个目录；`/record-decision` 把条目冻结成记录并删掉这个文件。

### proposal / rejected

一份记录另外两个生命周期：已设计未实现，以及考虑过并否决。

### class

一份记录路径上的第二根轴 —— 六个封闭取值之一（`feature`、`bug-fix`、`simplification`、`architecture`、`process`、`testing`）。类目不是 document kind。

### move

一份记录在生命周期之间发生的事：骨架在同一次改动里被改写，绝不追加。宿主叫法：同一次移动，写在它的 Agent Note 规则里。

### seal / archived / frozen

记录退役的三个词。**seal** 是动作 —— 文件的摘要、归档日期与理由进入 `.agents/notes/archived/manifest.json`；**archived** 是它住的地方；**frozen** 是后果 —— 已封印的记录永不编辑、重排、翻译、修补或移动。宿主叫法：`archived/`，三个意思相同。

### pair / sidecar / triplet

**pair**：英文正本与它的 `.zh.md` 对侧。**sidecar**：它们旁边那份 `.i18n.yaml` 一致性记录。**triplet**：三个文件全体，它们一起移动、一起重录（[i18n.md](i18n.zh.md)）。

### criterion

一份记录里的一条验收标准，带稳定编号，并点名它变红时会红的那条检查或改动面。没有归属的标准是愿望。

## 能力与契约

### capability

登记处里的一个 seam、core、service 或 bundle：消费者可以依赖的东西。它不是 document kind，也不是功能清单。

### seam

一个可替换的能力，带三个角色：Service Definition、一个或多个 Service Provider、一个或多个 Consumer。seam 是完整的能力，绝不是其中一个角色。宿主叫法：相同，且宿主同样把这个词保留给这个意思。

### registry

给能力分类的那份文件（`dev/capabilities/registry.json`），与源码里的 `capability: <key>` 标记保持同步。

### contract

一份接口，某个方向它的调用者负有义务，并且住在编译器能比对的地方。文字链向它；文字绝不复述它。

### mirror

在文档里逐字展示、并在 `dev/contracts/mirrors.json` 登记过的块，于是粘贴与它的源区间会被比对。mirror 是带精确性要求的投影。

### projection

生成器从单一源产出的任何东西 —— 检查目录、配对记录、证据记录。投影永不手改：改源，然后重跑生成器。

## 语言与配对

### canonical

一对文件里被另一侧翻译的那一侧：这里是英文，也是受宿主管辖的记录唯一被比对的一侧。它不是"正确"的同义词。

### counterpart

一对文件里的另一侧 —— 英文文件旁边的中文文件。

### switcher

成对文档顶部那行 `English | [中文](…)`。它被排除在配对的链接签名之外，因此可以两侧不同。

### identifier

在两种语言里逐字节保持相同的字符串：路径、命令、check id、surface 名、类目、验收编号，以及本术语表里的术语标题。标识符是文档中不被翻译的那部分。

### prose

文档里的自然语言文字，与它的标识符、代码、表格、配置相对。正文会被翻译、只写当前态，并且带预算。

### first occurrence

一份文档里某个术语的中文写法唯一一次带括注的位置；之后出现都用较短形式（[i18n.md](i18n.zh.md#术语)）。

## 禁用的写法

清单是可执行的，所以它住在执行它的那条检查旁边：`banned-spellings`，在 [tools/workflow.json](../tools/workflow.json) 里，并渲染进 [check-catalog.md](check-catalog.md)。每一条都是本仓库已经停止使用的写法；只有语料先干净下来才允许加一条禁令，所以这条检查永远不会带着红灯发布。
