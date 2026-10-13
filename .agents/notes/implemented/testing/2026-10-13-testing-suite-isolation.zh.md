# Decision: 测试套件隔离 —— 在 HTTP 边界打桩，并让桩自己证明被调用过

[English](2026-10-13-testing-suite-isolation.md) | 中文

Status: implemented
Date: 2026-10-13
Class: testing

## 问题

一个 docstring 承诺「全部离线」的测试模块，每次运行都在跟第三方索引说话。两个集成用例
patch 了 `search_pool.search_api`，但 `fetch_query_pages(..., search=search_api)` 是在
**定义时**绑定这个默认值的，于是 patch 永远到不了 pager，真实 HTTP 在一个声称离线的
测试下照发。用 socket 钩子实测：该模块每次运行产生 96 次到 `m.diao.im` 的真实连接、
耗时 39–50s，把测试里的搜索词发给了第三方。同一个坑还骗过一个**为核查这件事而写的**
独立审查脚本 —— 正因如此它值得一条记录而不是一句注释：隔离被写在正文里，而本该抓住它的
那条断言根本不存在。

## 决策

测试对索引的访问一律在 HTTP 边界打桩，并且**断言桩被调用过**。

- `tests/test_backend_integration.py` 用 `FakeIndexHttp` 替换
  `search_pool.urllib.request.urlopen` 提供夹具页，并在每个调用点断言桩确实被问到
  （`assertTrue(index_http.requests)`）—— 于是被绕过的桩会让测试失败，而不是静默通过。
- 这个写法不是新发明：`tests/test_backend_sources.py` 早就在那个边界打桩，并在 docstring
  里写明「patch 模块属性到不了 pager」。本决策把它变成集成模块也必须遵守的规矩。
- `backend/search_pool.py` 不动 —— 修复落在测试侧，因为定义时绑定是正确的生产行为，
  而做出虚假承诺的是测试。

## 曾考虑的替代方案

**去掉 `fetch_query_pages` 的默认值，改在调用点显式传 `search`。** 那样只要 patch 模块
属性就能注入；但那是为了迁就测试去改生产代码，而测试侧的修复可以完全不碰生产路径。

**保留模块属性 patch，把 docstring 改准。** 否决：测试仍然会发出真实查询，而「打偏的
patch」这一失效模式依旧不可见。

**删掉集成模块，只留各模块自己的测试。** 否决：与本记录同批的另一个决策所需的端到端
性质（外部候选进入排名）只存在于那一层，删掉是降低覆盖而不是消除陷阱。

**相信 docstring，不给桩加断言。** 否决：那正是本记录存在的目的。

## 后果

该模块从 39–50s 降到约 10s，且不再发出任何外部调用；整份套件从约 50s 降到约 26s，
而一台到不了该索引的机器跑的是同样的测试，而不是 96 次快速失败。代价是多一份夹具要维护、
每个调用点多一条断言；而且这个守卫只覆盖它打桩的东西 —— 来自子进程的泄漏连接或一个
已经打开的套接字，钩子看不到。另一个相关的坑现在写下来给后来人：
`tests/test_backend_integration.py` 还有一个调用点的阶梯对它那个片名是空的，所以它今天的
`assertTrue(requests)` 无法失败；承担无条件断言的是非严格那个调用点。

## 验证

- [C1] `tests` —— `tests/test_backend_integration.py` 承载边界桩与「桩被调用过」的断言；
  把桩换回模块属性 patch 会得到 192 次外部事件与三条失败的断言。
- [C2] `source` —— 共享这条接缝的一对守卫，其红证明必须**两侧同时**回退
  （`search_pool.py` 的写入侧与 `probe_magnets.py` 的读取守卫）；只回退一侧仍然绿，
  这一点被记下来，以免复核者把它读成「没有牙的断言」。
- [C3] `tests` —— `tools/check-invariants.py` 维持周边规则；第一版用 `time.sleep`
  而不是 `os.utime` 做同步的草稿被那条「禁止基于时间的测试同步」规则拒掉了。
