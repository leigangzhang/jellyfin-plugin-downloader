# Decision: Candidate sources — page the index honestly, add a second magnet source that carries seeders

English | [中文](2026-10-13-ranking-sources.zh.md)

Status: implemented
Date: 2026-10-13
Class: architecture

## Problem

The pool was small because the collector stopped early, not because the index was empty.
Each keyword took at most two pages of ten, and a keyword stopped contributing once twelve
usable candidates existed; the API's own `has_more` field was never read. Measured against
the same endpoint, one keyword could yield 133 (a film) or 210 (a series) unique
candidates — the pipeline was taking about 11% of what was available. The same index
returns no seeder count, so every candidate had to be probed for its own metadata, which is
what made the "speed" signal both expensive and unreliable. And because the second
available channel returned netdisk links rather than magnets, roughly 27% of a real pool
could not be downloaded directly at all.

## Decision

Two changes, both in `backend/search_pool.py` plus one new module.

- **Paging is driven by the response, not a constant.** `fetch_query_pages` reads
  `data.has_more`, bounds itself with `--pages` (default 8) and
  `--page-budget-secs` (default 45, wall clock), stops after two consecutive pages that add
  nothing, tolerates `--page-errors 2` failing pages, clamps each request timeout to the
  remaining budget, and runs `--page-workers 4`. The keyword ladder puts the alias /
  original title with its year ahead of the bare title.
- **A second magnet source that carries seeders.** `backend/torrents_csv_source.py`
  (stdlib only) returns magnet entries with `seeders` and a snapshot age. External entries
  enter the pool with `external_seeders` and `external_seeders_age_days`, a channel name in
  `external_sources` (never in `sources`, which the probe owns), no `metadata_at` (only a
  candidate with a readable file list may carry that stamp), and a `release_key` that marks
  duplicates without merging them (`--collapse-releases` is off by default).
- The console passes `L1_MAX_QUERIES = 12` and `L1_KEYWORD_BUDGET_SECS = 36` explicitly,
  so the L1 ladder's worst case is `12 × 36 = 432s` against the console's
  `SEARCH_POOL_TIMEOUT = 600s`.

## Alternatives considered

**Keep the single source.** Declined: the pool's ceiling was a constant in our own code,
and against an index that answers with a page cap of ten the difference is 20 candidates
versus 80–210 for the same keyword.

**Deepen paging for every keyword.** Declined: measured cost is not uniform — a clean
keyword paged to 90 entries in 10.1s at 93% relevant, a polluted one took 115.4s for 3.3%
relevant. Keyword precision is the cheaper lever, so the ladder was reordered and paging
kept bounded.

**Promote the netdisk channel instead.** Declined for now: those entries carry no readable
file list, so each one costs a metadata fetch, and they arrive as links the pipeline cannot
download without a transfer step.

**Add RSS channels (dmhy, mikan, acgnx).** Deferred, not rejected: they return magnets in
volume but only for anime, which is a coverage decision about the library rather than a
cheaper version of the same channel.

**Merge duplicate releases by default.** Declined: measured on a real keyword it collapses
80 candidates to 71, which trades the pool growth this decision bought for a tidier list.

## Consequences

Candidate volume for one title rose from 20 to 80 in a re-run of the same keyword; the cost
is 10–180s of request time per round depending on how polluted the keyword is, and that
time is now bounded rather than assumed. The second source's seeders are a snapshot that
can be weeks old — a 22-day-old dump is treated as a weaker signal, not a fresh one. A
keyword that the endpoint throttles can still return only 10–44 candidates, so the 3×
floor depends on the alias/year ladder being available for that title.

## Testing

- [B1] `tests` — `tests/test_backend_sources.py` pins paging (a `has_more` short-circuit,
  the budget clamp, the stop rule, the ladder order) and the external-source mapping, all
  offline with the HTTP boundary replaced.
- [B2] `tests` — the same file pins that an external candidate survives the metadata gate
  and reaches the ranking, which is the end-to-end property this decision exists for.
- [B3] `source` — `tests/test_backend_search_budget.py` pins that a keyword's progress is on
  disk before the process dies, the reason a killed round no longer loses what it found.
- [B4] `tests` — the worst-case ladder is asserted against `SEARCH_POOL_TIMEOUT` in
  `tests/test_backend_probe.py`, so the budget relation cannot drift silently.
