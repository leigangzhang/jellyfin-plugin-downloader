# 提案：gates —— 检查的扫描面跟着它治理的东西走，扫描面缩了就要变红

[English](2026-10-14-gates-scan-surface-follows-what-it-guards.md) | 中文

Status: proposed
Date: 2026-10-14
Class: process

## 问题

维护这个仓库的人，在一次结构性重构做完之后，无法判断各道检查是否还盖着原来盖住的东西。今天唯一的办法是逐份人工重读守卫 —— 而这份阅读正是重构本该省下来的。

一次重构里有三处改动各自缩小了某道守卫的扫描面，三处都没有被检查发现：

- `tests/backend_sources/test_backend_sources_dependencies.py` 的 `DependencyTests` 在 `search_pool.py` 变成七个文件之后，仍只枚举 `search_pool.py`、`torrents_csv_source.py`、`source_config.py`。原来 79021 字节里的 53722 字节（68%）落在 stdlib-only 守卫之外，于是把 `import requests` 写进 `backend/search/search_pool_api.py` 是绿的。
- `tests/backend_retired/_backend_retired_helpers.py` 的 `backend_modules()` 只扫一层。22 个模块搬进 `backend/{probe,search,pan,download}/` 之后，退役符号扫描读到 6 个文件而不是 28 个。
- `source` 改动面的语法证据是 `python3 -m py_compile backend/*.py` —— 一个只够到根目录的 shell glob，同样那 22 个文件脱出了它。

代价不在那三处修补，每处都只有几行；代价在于三处全是事后由人去读守卫才发现的，而且在同一次重构里同一形状复发了三次。一道悄悄停止守卫的检查报绿，而绿会被读成「已覆盖」—— 失效恰好藏在读者寻找保证的地方。

## 提案

给每一道「扫描一组文件、而不是点名一个文件」的守卫立两条规矩。

1. **扫描面是推导出来的，不是列出来的。** 守卫从它治理的对象算出自己扫什么 —— 递归遍历它负责的目录、读取已声明的清单、或它所断言的那组模块。在一个装着二十八个文件的目录旁边列三个文件名，这是缺陷，不是风格偏好。
2. **扫描面有下限。** 守卫断言它刚推导出的扫描面不小于它写下时的规模。于是 glob 不再命中、目录被改名、文件挪到守卫够不到的地方，都会变红，而不是空转通过。

落到本仓库，这次改动点名的文件是：

- `tools/workflow.json` 的 `surfaces[source].evidence` —— 已改成 `python3 -m compileall -q backend`，它会走整棵树。保持递归，并保留说明原因的注释。
- `tests/backend_sources/test_backend_sources_dependencies.py` —— 已改成 `BACKEND.rglob("*.py")` 并放行包目录名，另带 `>= 10` 下限。
- `tests/backend_retired/_backend_retired_helpers.py::backend_modules()` —— 今天已递归，但没有下限。这是需要补的那一处。
- `tests/backend_retired/test_backend_retired_paths.py` —— 遍历一个扫描面，结尾是 `assertEqual(offenders, [])`；今天空扫描面也会通过。
- `tests/backend_retired/test_backend_retired_backend_contract.py` —— 同一形状、同一位置（`offenders`），同样缺前置条件。
- `tests/AGENTS.md` —— 把这两条规矩作为一小段写进现有的「按主题分组」说明旁边，让下一道守卫照这个写，而不是从最近一次事故里反推政策。

不新增 check 类型、不新增工具：下限就是一条普通的断言，而这条断言就是会变红的那道检查。

## 曾考虑的替代方案

**在 `tools/check-invariants.py` 里新增一个 `scan-surface` check 类型。** 它能把规矩收在一处，而不是散进每道守卫，这一点确实有吸引力。它落选有两条理由：引擎的 check 类型是封闭集合、各自带自检；而且一个「递归走一遍」的 check 终究要逐守卫判断什么叫「扫描面」—— 守卫自己知道，让它在 `workflow.json` 里再声明一遍，就是第二份会漂移的事实。若出现第四次复发，或各处的下限开始被逐个删掉，应重新考虑。

**交给审查轮去抓。** 事实正是如此，而这也正是本提案存在的原因：那 68% 的缺口是审查者在工作已经合并、已经汇报之后才发现的。审查者适合抓「决定做错了」，但不该是唯一能发现「守卫已经什么都不扫」的地方。它落选，是因为需要察觉的那个读者，恰是这条规矩本该为之减负的那位。

**继续列路径，每拆一次就手工往名单里补。** 最省事的选项，也是这次重构之前事实上的做法。它按证据落选：在它之下，一次重构里有三份名单过期，而维护者读一遍名单，分不出它完整还是过期。

## 验收标准

- [G1] 把一个模块移到 `backend/` 下的新目录后，`source` 的两条命令（`python3 -m compileall -q backend`、`./build.sh`）无需改动就仍覆盖它；把同一模块移出 `backend/`，两条命令至少一条失败。
- [G2] 把任一受守卫的扫描面缩到根目录一层 —— 用 `glob` 代替 `rglob`，或让 offenders 集合为空 —— `tests` 至少一条用例失败；即扫描面塌陷是红的，而不是空转的绿。
- [G3] 规矩落地后，`workflow` 的自检（`python3 tools/check-invariants.py --self-test`）仍然通过，说明不是靠改坏声明来满足这次改动。

## 风险

**当那份「搬迁等价性」脚本第三次需要重写时，重新审视。** 证明一次重构没有改变行为 —— 逐字对比搬走的定义，并查找不可达与重复语句 —— 目前是 `/tmp` 下的临时脚本，这次重构写了两遍、丢了两遍；第二版缺了不可达检测，靠人工走查才发现缺口。那是一个独立的决策，有它自己的落选替代（继续当临时脚本用），因此不在本提案里决定。

**当 30 KB 警戒线开始把本该在一起的文件切开时，重新审视。** 阈值本身是另一个决策，且目前没有观察到它带来的危害。

**当 `probe/probe_health.py` 这类前缀开始在检索里造成歧义时，重新审视。** 模块进了包之后是否保留完整名字，是一个命名决策，它不推迟任何能力，今天也没有消费者在要求它。

**当 `openPanel` 下一次需要改动时，重新审视。** 有一个函数超过 200 行规矩，而拆它就会改变浏览器收到的字节，这是本次拆分被要求保持的。它是已知例外，需要单独一轮带视觉验证，而不是本规矩里的一个条款。

**递归扫描面会伸得太远。** 遍历 `backend/` 也会把生成物或 vendored 文件卷进来（若将来出现）。下限不该为了迁就噪声而上调 —— 排除项属于守卫内部，并紧挨着理由。

**下限是一个可能写错的数字。** 若模块被合法合并，按旧数量写死的下限会让一棵正确的树变红。下限是防塌陷的下界，不是普查数字；当守卫的对象真的缩小时，下限在同一次改动里跟着移动，并由提交信息说明。
