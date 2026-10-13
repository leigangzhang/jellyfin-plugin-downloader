# Decision: Ranking signals — external seeders carry the speed signal, a measurement only adds

English | [中文](2026-10-13-ranking-signals.zh.md)

Status: implemented
Date: 2026-10-13
Class: architecture

## Problem

Choosing a release is only as good as the numbers it is chosen by, and one of those numbers
was not a measurement. A ten-second `aria2c` window reported throughput ratios of
1.16–8.50× between two back-to-back probes of the same info hash, and its ranking across
hashes did not survive a repeat (`ρ = −0.214`). Worse, the speed subscore was
asymmetric: `_speed_score` returned the neutral `0.40` for a candidate whose metadata
resolved but whose window saw zero bytes *before* it looked at seeders, and
`measured <= 0.001` scored `0.02` — below the `0.05` given to a dead swarm. A candidate
with 141 seeders that happened to be probed therefore ranked below the same candidate
that was not probed, and only about 3% of probe attempts (5 of ~150 across 19 rounds)
ever recorded throughput at all. The speed dimension was constant for 43% of a real pool
(147 entries at 0.40, 81 at 0.05).

## Decision

The measured-throughput probe is no longer a ranking input. It can only raise a score.

- `backend/candidate_score.py` (`_speed_score`, `_seeds_prior`, `_speed_floor`) computes a
  seeders/peers prior that is a floor, never a penalty; `SPEED_UNMEASURED_NEUTRAL = 0.40`
  applies where no signal exists and `SPEED_DEAD_SWARM = 0.05` only where a live announce
  proved the swarm dead.
- The same file consumes an external seed count carried in the pool as
  `external_seeders` plus `external_seeders_age_days`: it is consulted only when announce
  has no reading at all, it is discounted by `EXTERNAL_SEEDERS_FRESH_DAYS = 7`,
  `DECAY_PER_DAY = 0.02`, `MIN_DISCOUNT = 0.5`, and its result is floored at the neutral
  `0.40`.
- `backend/search_pool.py` writes those two fields (and never writes `seeds`, which
  announce owns); `backend/probe_magnets.py` carries them onto the scoring record and
  derives a dead-swarm verdict from peers, not from a probe.
- `backend/speed_probe.py` remains as a manual diagnostic. `backend/console_server.py`
  no longer passes `--speed-probe` on its normal path.

## Alternatives considered

**Lengthen the measurement window.** A 30s or 60s window repeats better, but it does not
predict: one hash moved 6.865 → 1.452 → 0.0003 → 87.473 MB/s inside thirty minutes, and
longer windows cost 37–75s per candidate against a budget that also has to probe coverage.

**Repeat the probe and take a median.** It averages noise instead of removing it, and it
multiplies the fixed cost at a time when only about 3% of probes returned any bytes.

**Keep measurement and tune its parameters or weight.** Adjusting `--speed-top` or
reweighting still multiplies a noisy term by a nonzero coefficient; dropping the dimension
entirely would lose the dead-swarm verdict the pipeline needs before it submits.

**Give a measured candidate a small evidence margin.** Built and reverted: the margin is
invisible exactly where it was wanted (a saturated prior at 1.0 clamps it away), and in
the 0.4–1.0 band it raised candidates above their floor by three points — reintroducing
noise into the ranking.

**Route the speed decision to Thunder's own ETA.** Declined: a trial submit contaminates
the real download environment, concurrent trials collide, and it depends on the client
being up.

## Consequences

The speed dimension keeps its `0.28` weight and its `0.40` neutral value; `0.05` is now
reserved for a proven dead swarm. Among the strongest swarms — `seeds ≥ 120`,
`peers ≥ 200` — the prior saturates at 1.0, so speed stops distinguishing those candidates
and the other three dimensions decide. That is deliberate: revisit it only by changing
`seeds_reference`/`peers_reference`, which changes the scale every candidate is read on.
A pool entry written before this decision carries the old field names
(`seeders`, `seeders_snapshot_age_days`), which no read path recognises; its external
signal stays silent until the title is searched again.

## Testing

- [A1] `tests` — `tests/test_backend_scoring.py` pins the floor semantics: a measured
  candidate never scores below the same candidate unmeasured, a zero measurement never
  reaches the dead-swarm value, and a saturated prior ties on purpose; the suite runs it in
  `python3 -m unittest discover -s tests`.
- [A2] `tests` — `tests/test_backend_probe.py` pins the wiring from a pool entry's external
  seeders through the probe's scoring record into the rank, plus the guard that a round
  which wrote no probe JSON is not re-presented as the current one.
- [A3] `tests` — the end-to-end shape is checked offline in
  `tests/test_backend_sources.py` with the network stubbed at the HTTP boundary, so no
  criterion here depends on a live swarm.
- [A4] `source` — the cost of the surviving probe path per round is bounded by
  `tests/test_backend_search_budget.py` and the budget guard in `probe_magnets.py`.
