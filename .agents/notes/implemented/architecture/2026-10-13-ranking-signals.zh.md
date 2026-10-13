# Decision: 排序信号 —— 外部做种者承载速度信号，实测只能加分

[English](2026-10-13-ranking-signals.md) | 中文
Status: implemented
Date: 2026-10-13
Class: architecture

## 问题

选片好不好，取决于拿什么数来选；而其中有一个数并不是测量出来的。一次十秒的
`aria2c` 窗口，对同一个 info hash 的两次背靠背探测给出的吞吐比是 1.16–8.50×，
它跨 hash 的排序也经不起重复（`ρ = −0.214`）。更糟的是速度子分的**不对称**：
`_speed_score` 会在看种子数**之前**就为一个「元数据能解析、但窗口内 0 字节」的候选
返回中性 `0.40`，而 `measured <= 0.001` 给 `0.02` —— 比死种拿到的 `0.05` 还低。
于是「141 个种子、恰好被抽中去测速」的候选，排名低于「同样种子数、没被抽到」的
候选；而全部探测里只有约 3%（19 轮约 150 次中的 5 次）真的记录到了吞吐。速度维度
在真实池里有 43% 是常量（147 条恒 0.40、81 条恒 0.05）。

## 决策

实测吞吐探针不再参与排序，它只能把分数抬高。

- `backend/candidate_score.py`（`_speed_score`、`_seeds_prior`、`_speed_floor`）把
  seeds/peers 先验算作**下限**而非惩罚；没有信号时用 `SPEED_UNMEASURED_NEUTRAL = 0.40`，
  只有 announce 证明 swarm 已死时才用 `SPEED_DEAD_SWARM = 0.05`。
- 同一个文件消费池里的外部种子数 `external_seeders` 与
  `external_seeders_age_days`：只在 announce **完全没有读数**时才作为兜底，按
  `EXTERNAL_SEEDERS_FRESH_DAYS = 7`、`DECAY_PER_DAY = 0.02`、
  `MIN_DISCOUNT = 0.5` 折扣，结果不低于中性 `0.40`。
- `backend/search_pool.py` 负责写这两个字段（且**不写** `seeds` —— 那属于 announce）；
  `backend/probe_magnets.py` 把它们带进打分记录，并且死种判定取自 peers，不取自探针。
- `backend/speed_probe.py` 保留为手动诊断工具；`backend/console_server.py` 的正常路径
  不再传 `--speed-probe`。

## 曾考虑的替代方案

**把测量窗口加长。** 30s/60s 的重复性更好，但它不预测：同一个 hash 在半小时内走了
6.865 → 1.452 → 0.0003 → 87.473 MB/s；而且更长窗口每条要 37–75s，而预算还要用来覆盖
候选。

**重复探测取中位数。** 那是把噪声平均掉而不是消掉，还在「只有约 3% 探测能拿到字节」
的前提下把固定成本乘了好几倍。

**保留测量、改参数或改权重。** 调 `--speed-top` 或重新加权，仍然是把一个带噪的项乘以
非零系数；而整条维度拿掉会丢掉流水线提交前需要的死种判定。

**给「确实测到过」加一点证据余量。** 试过并撤回：余量恰恰在需要它的地方不可见
（先验饱和到 1.0 时被夹掉），而在 0.4–1.0 的中间带把候选抬到 floor 之上三点 ——
等于把噪声请回排序。

**把速度判断交给迅雷自己的 ETA。** 否决：试跑会污染真实下载环境、并发试跑互相冲突，
而且依赖客户端在线。

## 后果

速度维度保留 `0.28` 权重与 `0.40` 的中性值；`0.05` 现在只留给被证实的死种。在最强的
那批 swarm（`seeds ≥ 120`、`peers ≥ 200`）上先验会饱和到 1.0，速度因此不再区分它们，
由另外三个维度决定 —— 这是有意的；要改只能改 `seeds_reference`/`peers_reference`，
而那会改变**每一个**候选被读到的刻度。本决策之前写出的池条目带的是旧字段名
（`seeders`、`seeders_snapshot_age_days`），没有任何读路径认识它们，其外部信号要等
该片名被重新搜索才生效。

## 验证

- [A1] `tests` —— `tests/test_backend_scoring.py` 钉住下限语义：有实测的候选永不低于同一
  候选未实测的分数、零实测永不落到死种值、饱和先验刻意打平；由套件通过
  `python3 -m unittest discover -s tests` 运行。
- [A2] `tests` —— `tests/test_backend_probe.py` 钉住从池条目的外部种子数、经探针的
  打分记录、到排名的接线，并钉住「本轮没写出 probe JSON 时不得把上一轮当成本轮」的守卫。
- [A3] `tests` —— 端到端形态在 `tests/test_backend_sources.py` 里离线检查（网络在 HTTP
  边界被打桩），所以这里的任何一条判据都不依赖活的 swarm。
- [A4] `source` —— 保留下来的探针路径每轮最多花多少，由
  `tests/test_backend_search_budget.py` 与 `probe_magnets.py` 的预算守卫共同界定。
