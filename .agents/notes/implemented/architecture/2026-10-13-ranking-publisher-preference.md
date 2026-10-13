# Decision: Publisher preference — a bounded bonus on the total, and a ladder tier that can reach the source

English | [中文](2026-10-13-ranking-publisher-preference.zh.md)

Status: implemented
Date: 2026-10-13
Class: architecture

## Problem

Two shows were fetched in one session with the same pipeline, and the first-ranked
candidate for one of them was slow while the other ran at full speed. The selection had no
probe input in either case (`measured_speed_mbps` was null for both), so the ranking rested
on announce seeders — which disagreed with the outcome: the candidate with 105 seeds
produced no bytes in a 30-second window, while a candidate with 10 seeds sustained
9.3–9.7 MB/s. Worse, the faster release was not reachable by title at all: the publisher
names that title `周三俱乐部`, so a search for the show's actual name cannot return it,
and the pool for that show contained none of that publisher's releases. The user's own
record — more than ten downloads from the same publisher, all usable — pointed at the same
conclusion the machine-side measurement did.

## Decision

A publisher is preferred by name, in two places, and both are bounded.

- **Scoring** (`backend/candidate_score.py`): a candidate whose name contains
  `DDHDTV_SOURCE_TOKENS` (`ddhdtv`, `高清剧集网`) receives
  `DDHDTV_SOURCE_BONUS = 4.0` added to the **total** score, recorded as
  `breakdown["source_preference"]`. It is not added to any dimension, and the four
  dimension scores are bit-identical with and without it.
- **Search** (`backend/search_pool.py`): the ladder gains a `publisher` tier that queries
  `<name> DDHDTV`, placed after alias/year tiers and before the bare-title tier, skipped
  for pure-CJK titles (the token is ASCII) and skipped in strict season mode. The tier
  exists because the publisher's releases are frequently not reachable by the show's own
  name.

## Alternatives considered

**Do nothing, let seeders decide.** That is the state the measurement falsified: 105
announced seeds produced no bytes while 10 produced 9.3–9.7 MB/s. The public swarm cannot
see the client's offline/cloud acceleration path, which is what the publisher's repacks
appear to reach, so the signal was never measuring what the choice depends on.

**Add the bonus to the experience dimension.** Tried and reverted within the same change:
a real DDHDTV candidate already sat at 99.9/100 experience, so the dimension clamped the
bonus and it was worth 1.3 points instead of the intended 4.4. A preference that vanishes
exactly on the candidates it targets is worse than none, because the code reads as if it
works.

**A larger bonus that makes the publisher win outright.** Declined: at 4.0 the preference
lifts one-step resolution differences but loses to a two-step gap with comparable bitrate
(1080p 78.79 vs 480p + bonus 72.58), which is the intended shape — a preference, not a
replacement for selection.

**Route the choice through the download client's own ETA.** Declined earlier and still
declined: it requires submitting to the live client, which the user rejected as
contaminating a real download environment and colliding with concurrent tasks.

**Drop the speed probe entirely.** It is already off in the console path (the probe flag
was removed in `f51ac82` and measured null for both shows here), so removing it changes
nothing about this failure: with no probe the ranking still picked the slow candidate.
The probe stays as a manual diagnostic; what changed is that nothing depends on it.

## Consequences

The publisher is preferred only where its name appears, so a pool without it ranks exactly
as before — this decision cannot fix a show whose releases are absent. Among same-title
candidates it lifts the preferred source past one resolution step, and it is capped and
auditable, so it can be retuned or removed by editing one constant. The 4.0 figure comes
from one measured pair plus the user's ten-download record, not from a corpus; the honest
route to a defensible number is the calibration loop this batch does not yet have —
writing the client's measured speed back into the pool per candidate, so the preference
stops being an assumption and starts being a statistic. There is no such thing as a
publisher-freshness signal here: the name is the only carrier, because the index returns
names and nothing else about who published.

## Testing

- [D1] `tests` — `tests/test_backend_scoring.py` pins that the bonus adds exactly
  `DDHDTV_SOURCE_BONUS` to the total, records it in `breakdown.source_preference`, and
  leaves all four dimension scores identical with and without it.
- [D2] `tests` — the same file pins the bound by bitrate adequacy rather than by resolution
  label: a two-step gap (1080p vs 480p) with comparable bitrate still wins, and the test
  records why a label-based comparison misleads.
- [D3] `tests` — `tests/test_backend_sources.py` pins the ladder tier's position and its two
  skips (pure-CJK name, strict season mode).
- [D4] `source` — the end-to-end shape was measured, not asserted: `挑情丑闻` searched with
  the console's own parameters reaches the ladder's publisher tier and the publisher's
  release enters the pool, which the previous release could not do.
