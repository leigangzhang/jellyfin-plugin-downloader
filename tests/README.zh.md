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
| `test_policies.py` | 政策所有者：每份常驻文档都带着它那条检查所要求的形状，且声明与文档一致 |
| `test_records.py` | 记录层：类目目录、发布、封存，以及一次改动会认领的改动面 |
| `test_pairing.py` | 配对层：每对一条记录、重算而非信任、两侧形状一致，以及切换器指向对侧 |
| `test_evidence.py` | 证据运行器：它绝不能把一句正文或一个占位符当成命令 |
| `test_tiers.py` | 档位开关：开关、树与地图的档位表必须在两个方向上都一致 |
| `AGENTS.md` | 在这里加一条测试的规矩 |
| `README.md` | 本页 |
