# 套件自己的测试

[English](README.md) | 中文

这套测试证明的是套件自己的机器：每条守卫都能失败、声明的与语料一致、工具守住了它们的契约。测试政策住在 [docs/testing.md](../docs/testing.md)，在这里加一条测试的规矩住在 [AGENTS.md](AGENTS.md)。

与套件其它命令一样，请在仓库根目录执行：

```sh
python3 -m unittest discover -s tests
```

## 目录结构

| Path | What it is |
|---|---|
| `__init__.py` | 让本目录成为包，模块之间便能按名字导入 |
| `harness.py` | 各模块共享的东西：仓库根、按路径加载工具的加载器、以及标题读取器 |
| `kit/` | 套件自身的机件：[政策](kit/test_policies.py)、[记录](kit/test_records.py)、[配对](kit/test_pairing.py)、[证据](kit/test_evidence.py)与[档位开关](kit/test_tiers.py) |
| `backend_sources/` | L1 取源链：索引翻页、关键词阶梯、季号识别、发行名键、池合并、外部候选 |
| `backend_retired/` | 退役路径、站点配置、站点 URL 分类，以及读插件页面自身源码的那批守卫 |
| `backend_probe/` | 探针链：打分、软预算、离线干跑、集成契约与清理守卫 |
| `fixtures/` | 离线用例复放的裁剪真实响应 |
| `AGENTS.md` | 在这里加一条测试的规矩 |
| `README.md` | 本页 |
