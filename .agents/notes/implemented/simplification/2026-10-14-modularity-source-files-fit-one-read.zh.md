# Decision: modularity —— 一份源文件一次读完，一个域一个目录

[English](2026-10-14-modularity-source-files-fit-one-read.md) | 中文

Status: implemented
Date: 2026-10-14
Class: simplification

## 问题

在这个仓库里干活的人，改一份文件里任何东西之前都得先把它读完整；而超过一次可读上限的文件读不完整 —— 它只能分片回来，每一片都是一次完整往返，把前面几片已经说过的内容再发一遍。有十一个文件越过了 30 KB 警戒带，其中五个越过了读者自己的上限；而代价是量出来的，不是估的。

- `backend/probe_magnets.py`，58.5 KB / 1530 行 —— 一个成员读了 27 片，仍只覆盖约 90%；仅这一段阅读就白花约四分钟往返，而这份文件从来没有人需要一次性改它。
- `backend/search_pool.py` 67.3 KB / 1570 行、`tests/test_backend_sources.py` 70.5 KB / 1430 行、`tests/test_backend_retired_paths.py` 64.0 KB / 1208 行、`Jellyfin.Plugin.JellyfinDownloader/Web/inject.js` 79.6 KB / 1874 行 —— 每个读它们的人都撞上同一形状。
- 这些文件不是注释冗余：`probe_magnets.py` 注释只占 4%，没有可删的东西。稠密的是从未被归组的活代码；五个文件里有两个低于任何人会为之辩护的行数线，却仍然越过了字节上限。
- 这次工作的后半段，是卡在 30 KB 警戒带与 51200 B 闸门**之间**的六个文件 —— 闸门对它们一个都没响过：`backend/console_server.py` 40282 B / 932 行、`backend/media_download_lib.py` 39758 B / 1218、`backend/candidate_score.py` 31681 B / 868、`tests/backend_probe/test_backend_probe.py` 43364 B / 924、`tests/backend_probe/test_backend_integration.py` 32467 B / 643、`tools/pair-docs.py` 32883 B / 758。警戒带正是分片阅读继续存活的地方；而这六个里有三个还同时背着同一笔代价的另一半：函数嵌套五到七层（`console_server.py` 的 `_run_search_job` 6 层、`media_download_lib.py` 五个函数 5–7 层、`pair-docs.py` 的 `sections` 5 层），所以文件并不是读者必须整份拿住的唯一单位。

## 决策

上限说在它真正被度量的单位上，越线是**安放问题**而不是**删减问题**：一份需要读者整份打开的文件不得超过 51200 B，越过 30 KB 警戒带之后它是下一轮拆分的候选，而越过上限的文件按职责分开 —— 绝不按行号切 —— 住进一个以某个域命名的目录。`source-budget` 对 `backend/*.py`、`tests/*.py` 与 `Jellyfin.Plugin.JellyfinDownloader/Web/*.js` 声明了这条上限。

两部分都已建成。第一部分把 `backend/probe_magnets.py`、`backend/search_pool.py`、`tests/test_backend_sources.py`、`tests/test_backend_retired_paths.py` 与 `Web/inject.js` 搬进了 `backend/probe/`、`backend/search/`、`backend/pan/`、`backend/download/`、`tests/backend_probe/`、`tests/backend_sources/`、`tests/backend_retired/`、`tests/kit/` 与五个 `Web/inject_0*.js` 分片。第二部分把上面那六个文件搬进了各自的目录：

- `backend/console_server.py` 保留原路径，现在承载契约面与组合根；实现体在 `backend/console_server/{__init__,http_api,jobs,probe_state,service}.py`。
- `backend/media_download_lib.py` 已不存在。扁平导入 `media_download_lib` 照常解析，现在指向 `backend/media_download_lib/{__init__,paths,db,torrents,cleanup,media,finalize,watcher,_shared}.py`，其 `__init__.py` 再导出调用方导入的全部 37 个名字。
- `backend/candidate_score.py` 是 27 行的入口：引导 `sys.path`、再导出、转发 `__main__`；代码在 `backend/candidate_score/{__init__,policy,rules,release,speed,score,cli}.py`。
- `tests/backend_probe/test_backend_probe.py` 与 `tests/backend_probe/test_backend_integration.py` 已不存在。它们的用例在六个 `test_probe_*.py` 与四个 `test_backend_*.py` 兄弟模块里，共享夹具在 `_backend_probe_helpers.py` 与 `_backend_integration_helpers.py`。
- `tools/pair-docs.py` 是 32 行的入口；代码在 `tools/pairdocs/{__init__,model,render,checks,cli}.py`。

凡是被合约按路径点名的名字都保留了原路径。`BackendManager.cs:99` 按 `<插件目录>/backend/console_server.py` 启动；`uninstall.sh` 在 8 处 `pgrep -f` 里按 `console_server.py` 这个名字认进程；`tests/backend_probe/test_backend_scoring.py:28` 用 `spec_from_file_location` 加载 `backend/candidate_score.py`，且 `backend/` 不在 `sys.path` 上；`tools/kitcheck/links.py:24` 以同样的方式加载 `tools/pair-docs.py`，注册的模块名是 `pair_docs`。

有三处机制是这套布局逼出来的，且都是承重的：

- `backend/console_server/__init__.py` 先判断入口文件是否正以 `__main__` 运行，是则把 `sys.modules["console_server"]` 别名到它、**不再执行第二次**；否则才按路径加载入口。没有这个分支，脚本路线与 import 路线会各定义一份 `Handler`、`main` 与 `_search_status`。提交 `7315e39` 与 `b0d7abf`。
- `backend/media_download_lib/paths.py` 从包的父目录推导 `DATA_ROOT`，而不是从 `dirname(__file__)`，所以数据根仍然是 `backend/state`。
- 两个被拆的测试文件都把共享夹具搬进了下划线前缀的 helper，因为 `unittest` 会收集一个模块 import 进来的任何 `TestCase` 子类。

闸门之下最大的文件现在是 `Jellyfin.Plugin.JellyfinDownloader/Web/inject_05_render.js`（24565 B），最大的 `.py` 是 `backend/search/search_pool.py`（25883 B）；没有任何 tracked `.py` 超过 30720 B。

## 曾考虑的替代方案

**按行号切，而不是按职责切。** 这是唯一能精确命中体积的办法，而且从第一份需求起就摆在桌上，原话是「按行数机械切」。它落选是因为切开函数体会改变行为，而行为是唯一不许变的东西：这里每条验收标准都是相对未拆分的那棵树写的。当边界无法按职责画出时 —— `inject.js` 有一个函数 212 行，拆它就会改变浏览器收到的字节 —— 文件被留在函数规则的线外并记录为例外，而不是被切开。

**拆完保持平铺。** 这是第一轮的做法，而且它是诚实的：文件都在上限之下、套件全绿、什么都没坏。它栽在第二次阅读上。一个目录里二十八个脚本，意味着要找探针链的读者得搜整棵树，而一个没人读的目录，正是下一份超标文件长回来的地方。归组的代价是再多一次搬迁，外加一条合约：被按路径拉起的入口脚本，必须仍然找得到它导入的根。

**删掉而不是拆开。** 这些文件里没有死东西。对第一轮那六个模块做过一次普查：没有无人引用的顶层名字、没有只写不读的变量、没有不可达语句、没有被注释掉的代码，而会抓到这些的守卫本来就是绿的。删除从来不是一个可选项，因此记录为「考虑过但不可用」，而不是「权衡后落选」。

**`console_server.py` 的入口做成纯再导出。** 原计划说入口只做再导出与 `__main__` 转发；它栽在测试上。有五处断言把 `backend/console_server.py` 当**文本**读，并钉住里面的字面量 —— `SEARCH_TIMEOUT = 900`、`PROBE_BUDGET_SECS = 720`、`"--budget-secs"`、六个状态 token、`return "best_dead_swarm"`、`os.environ.get("JMD_PORT")` 与 `{**obj, "service": SERVICE_ID}` —— 所以入口必须保留契约面与组合根，搬走的只能实现体。

**给两个被拆的测试文件留一个再导出的聚合入口。** 它落选是因为 `unittest` 会把一个模块 import 进来的 `TestCase` 子类当成本模块的用例：聚合入口把搬走的类再导出，每个用例就会被报两次（315 → 约 630），直接违反「不得新增用例」这条标准。用例因此变成兄弟模块，共享夹具搬进 discovery 不收集的下划线 helper。

**把工具包命名为 `tools/pair_docs`。** `tools/kitcheck/links.py:11` 写下 `_PAIR_DOCS = "pair_docs"`，并把按路径加载出来的入口按这个名字注册进 `sys.modules`；同名包会让入口 import 到半初始化的自己而不是自己。包名是 `tools/pairdocs`。

**给两个测试文件新建同名嵌套包。** `a241dab` 已经按被测模块把 `tests/` 分了组，两个文件也已经在 `tests/backend_probe/` 里、而 `unittest discover` 会递归进去；兄弟模块保住了这个分组，新开一个 `tests/test_backend_probe/` 反而会把它撤掉。

**让 `backend/console_server/` 不带 `__init__.py`。** 这是本次工作前半段建造时记在案的替代：那样 `import console_server` 会直接解析到入口文件，根本不需要别名。它落选是因为包内模块将不得不以通用顶层名 —— `jobs`、`service`、`http_api` —— 进入 `backend/` 的导入命名空间，而那正是前半段要消掉的扁平树。

## 后果

警戒带里已经没有文件。没有任何 tracked `.py` 超过 30720 B，闸门之下最大的文件是一片 24565 B 的 `Web/inject_0*.js` 分片，而六个目标原来的 220435 B / 六个文件变成了 41 个文件、其中最大者是 `tools/pairdocs/model.py`（12523 B）。现在打开那十一个目标中任何一个的读者，只打开一个文件。

**同名冲撞是这套布局的价码。** `backend/console_server.py` 与 `backend/console_server/` 同名，是因为两条合约各钉住一个：插件按那个确切路径启动文件，而本决策把一个域的模块放进同名目录。Python 先解析目录，于是当入口已经作为 `__main__` 运行时，`backend/console_server/__init__.py` 必须把 `sys.modules` 重定向到入口。其余目标都不需要这一手，而这是整次重构里唯一一处「读者动文件之前必须先知道某个机制」的地方。

**再导出面现在是一条合约。** `backend/media_download_lib/__init__.py` 再导出调用方导入的 37 个名字，`backend/candidate_score/__init__.py` 再导出 54 个名字（含测试直接取用的下划线私有名）。这两个包内部改名，要么保住扁平名，要么在同一次改动里更新每个调用方。

**当某道检查的扫描面悄悄缩小却没有东西变红时，重新审视。** 这次决策搬动了 22 个后端模块与 31 个测试模块，过程中有三道守卫静默失去覆盖：一道 stdlib-only 守卫在 `search_pool.py` 变成七个文件后仍守着三个写死的文件名（68% 的字节在它之外）、一道退役符号扫描只扫一层、语法闸门是个只够到根目录的 shell glob。三处都在这里修好了，但「守卫是否必须从它治理的对象推导扫描面、并为塌陷带上限」是一个独立决策、有它自己的落选替代，因此不由本记录裁定。

**当本决策造出来的包落在某道闸门的扫描面之外时，重新审视。** 对 `source-budget` 这已经是事实：它的 patterns 是 `backend/*.py` 与 `tests/*.py`，只匹配一层，所以 `backend/console_server/*.py`、`backend/media_download_lib/*.py`、`backend/candidate_score/*.py`、`tests/backend_probe/test_probe_*.py` 与 `tools/pairdocs/*.py` 完全不在它度量的范围内 —— 促成这次工作的那道闸门，看不见这次工作产出的大部分。把它加宽是 `gates` 面上的改动，本记录有意不取。

**当超线的是函数而不是文件时，重新审视。** 200 行规则与字节上限会冲突：`inject.js` 里的 `openPanel` 是 212 行，而拆它就会改变交付字节，而本决策要求这些字节保持不变。它是已知例外，需要单独一轮带视觉验证，不是这里的一个条款。同一条规则在别处也仍未满足：25 个 tracked `.py` 嵌套超过四层 —— 其中 `backend/search/search_pool.py` 的 `collect` 是 189 行 / 7 层、`backend/download/submit_xunlei.py` 的 `main` 是 418 行 / 6 层 —— 而没有任何检查度量嵌套。

**当上限被抬高时，重新审视。** 51200 B 是读者自己的上限，不是偏好。这个数字随读者的上限移动，绝不能为了让「不拆更省事」的文件进来而移动。

**当模块名开始重复它所在的包名时，重新审视。** `probe/probe_health.py` 保留旧的平铺名，是为了让每一处引用、docstring 与测试的含义都不变；如果读者开始把两者混淆，包内改名是另一个决策。

**闸门度量的是字节，不是可读性。** 一份 40 KB 的稠密逻辑照样通过，照样要读很久；这正是 30 KB 警戒带存在的理由，而它故意不做闸门。警戒带里再加一道闸门，会对合法文件变红，并且一个月内就会被删掉。

**归组对共享代码不是免费的。** 被多个域导入的模块没法搬进任何一个域，否则它就成了跨域依赖；留在 `backend/` 根的那几个就是为此留下的，下一个共享模块也必须按这个理由判断，而不是按体积判断。

**未核实。** 本次改动没有跑 `install.sh`，因为它在复制前会先停 Jellyfin；这里没有任何东西证明插件侧被复制过去的 `backend/` 树在原地能跑起来。`JMD_DATA_DIR` 有端到端覆盖，但那是在工作树上跑的，不是在装好的插件目录里。

## 验证

- [M1] `source-budget` 对三个 pattern 组度量 UTF-8 字节，任一文件超过 51200 B 即失败；当前树通过，最大的受闸文件是 24565 B、最大的 `.py` 是 25883 B，那十一个目标已经不在了。
- [M2] `tests` —— `python3 -m unittest discover -s tests` 全绿，用例名与拆分前相同，没有用例被删除、跳过或放宽。
- [M3] `source` —— `python3 -m compileall -q backend` 与 `./build.sh` 通过，且启动器点名的每个入口脚本仍能跑起来：走控制台自己的启动器调用时，8 个全部 rc=0。
- [M4] `packaging` —— 构建出的 DLL 所交付的脚本与拆分前逐字节相同（`sha256 b8feedab…c206b6`，81462 B），即那个「被交付而非被导入」的目标是被证明的，不是被声称的。
- [M5] `tests` —— 两个被拆的测试文件保住了各自的用例标识：`python3 -m unittest discover -s tests` 报出的 `Class.method` 多重集在拆分前后完全相同，且仍然报 `Ran 315 tests`。正是这个数量让「再导出聚合入口」成为失败而不是风格选择。
- [M6] `source` —— 六个目标新落点里的每个文件都 ≤30720 B：41 个文件，最大者 `tools/pairdocs/model.py` 12523 B。**unpinned**：没有任何东西度量 30 KB 警戒带 —— `source-budget` 只在超过 51200 B 时响 —— 也没有任何东西度量嵌套，所以这一条是用一次临时 AST 扫描验证的，没有闸门会重复它。
- [M7] `source` —— 入口合约仍成立：`python3 backend/console_server.py --port <空闲端口>` 应答 `GET /api/snapshot`，返回 `"service": "jellyfin-downloader-backend"`；`python3 -c "import sys; sys.path.insert(0, 'backend'); import console_server"` 拿到的是**同一个模块对象**，不起服务、不读状态；`python3 backend/candidate_score.py --help` 退出码 0；`python3 tools/pair-docs.py --check` 报所有对同步。这三条里第一条的长期归属是 `source` 面上那条人工 Jellyfin 冒烟路径。
- [M8] `packaging` —— 插件把 `backend/` 按树交付，所以 `backend/console_server/`、`backend/media_download_lib/` 与 `backend/candidate_score/` 随 DLL 一起走，`install.sh` 的 `cp -R backend` 会把它们带上。**未核实**：`install.sh` 本身没跑，因此没有哪次运行证明被复制过去的树在原地可用。
