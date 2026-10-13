# Review: 1.0.0.8 取源与选种简化 —— teammate 两轮复核（2026-10-13）

English | [中文](2026-10-13-ranking-flow-simplification.zh.md)

## Claim

The 1.0.0.8 simplification claims that the station list and the L1 keyword combinations are
configuration (not constants), that the pool keeps exactly two channels (station magnets and
a station's web page feeding the cloud drive), that the configured keywords are the first
query tier, and that the pipeline is `gather → health → metadata → score → re-score → rank`
with the aria2c speed probe deleted.

## Evidence

Reviewer: the AgentTeams teammate `verifier`, which worked in **isolated clones**
(`/tmp/jmd-review` at `c0ba28a`, `/tmp/jmd-review2` at `7d4b976`) with `origin` removed and no
write access to this working tree; both clones' `git status --porcelain` stayed empty. This
record transcribes its two reports (task outputs `t1` / `t2` in the team's archive); the
captain is the author of the change and wrote only the dispositions below.

- `python3 -m unittest discover -s tests` in the clone → `265 tests OK` (round 1), `273 tests OK` (round 2) — the change does not break its own suite.
- `search_pool.py … --season 1 --strict-match` over a five-candidate fixture → round 1: only the explicit `S02` candidate was dropped (`STRICT_SEASON_FILTER {kept:4, dropped:1}`); round 2 (same fixture, current tree) → `{kept:1, dropped:4}`, dropping 「第二季」/`Season 2`/`第2季` too.
- A round where no non-API station can write a candidate file → round 1 measured `EXIT=4` with `/tmp/stale.magnets` byte-identical before and after (md5 unchanged), and the probe returning `2` before it read `--extra-candidates`.
- `probe_magnets.py` with a missing `--candidate-file` but a valid `--extra-candidates` → round 2: no `rc=2`, the cloud candidate reaches `ranked`.
- Nine mutations re-run by the captain with bytecode writing disabled (`python3 -B`, `__pycache__` cleared) → every guard went red as intended; the same runs performed with bytecode cached gave contradictory results, which is the correction named under *Deliberate risks*.

## Findings

Round 1 (`c0ba28a`) and round 2 (`7d4b976`); every one is closed unless marked open.

- **Blocker (round 1, closed in `7d4b976`)** — a round whose stations are all cloud-only lost every cloud candidate: `search_pool.py` exited 4 without rewriting the candidate file, the console still passed the fixed `/tmp/<slug>.magnets` to the probe, and `probe_magnets.py` returned 2 before reading `--extra-candidates`. Impact: the one surviving cloud channel delivered nothing. Evidence: `EXIT=4` with the stale file's md5 unchanged.
- **Blocker (round 1, closed in `7d4b976` + `237ee8b`)** — the same fixed path let the probe read the *previous* round's candidates. Impact: stale results presented as this round's. Evidence: file lands identical across rounds; the fix is now per-round identity (`/tmp/<slug>.<jid>.magnets`) plus a freshness belt.
- **Blocker (round 1, closed in `da91366`)** — strict season was toothless: the anchor tier ran before the strict branch and nothing downstream filtered by season. Impact: a season-1 search could surface another season. Evidence: the five-candidate run above.
- **Blocker (round 2, already fixed at the reported revision's successor)** — the filter recognised only `SxxEyy`/`Sxx`, so 「第二季」 and `Season 2` leaked. Evidence: round 2's `{kept:4, dropped:1}` versus `{kept:1, dropped:4}` after `da91366`.
- **Suggestion (round 2, closed in `237ee8b`)** — the retired-name AST guard was bypassed by `getattr(o, "measured_speed_" + "mbps")`, `"".join([...])`, `%`-formatting and `import speed_probe as sp`. Impact: a retired path could return unnoticed. Evidence: round 2's green run over those shapes.
- **Suggestion (round 2, closed in `237ee8b`)** — the strict filter ran only at the end of `main()`, while `collect` lands progress per keyword; a killed round left an unfiltered file.
- **Suggestion (round 2, closed in `237ee8b`)** — `CANDIDATE_LINES_SKIPPED` copied up to 60 characters of the user's file into the job log, and the "only scoring cloud candidates" line printed before the extras file was read.
- **Blocker (round 1 and 2, closed by the other session on `agent/config-page-layout`, merged as `0cd4b4a`)** — saving the configuration restarted the backend unconditionally and `BackendManager.KillByPort` killed every pid listening on that port, so setting the port to Jellyfin's own killed Jellyfin; `POST {"btSites":[null]}` raised a 500; the C# `IsHttpUrl` accepted values the Python side rejected. The branch replaces the kill with an incrementing port probe, makes the backend announce itself (`SERVICE_ID`) so a foreign HTTP service is not mistaken for it, rejects addresses the classifier cannot place instead of defaulting them to `diao`, and adds the kind round-trip the page needs.

## Follow-up

- **Became a check** — `tests` surface: `CloudChannelWiringTests.test_a_stale_candidate_file_is_not_fed_to_the_probe` / `test_each_round_gets_its_own_candidate_file`, `MissingCandidateFileTests`, `StrictSeasonFilterTests.test_a_killed_round_lands_already_filtered_candidates`, `SeasonDetectionTests`, `SeasonRecognitionHasOneHome`, and the fragment/import cases in `RetiredFragmentGuardTests`.
- **Became a rule** — the decision record `2026-10-13-ranking-flow-simplification` (Consequences: strict season is closed by explicit markers; Testing `[B10]`–`[B12]`), and the AST guard's own docstring now states what it cannot catch.
- **Deliberately nothing** — `"".join([...])`, `%`-formatting, `bytes` and variable-assembled names are not caught by the guard; enumerating them statically costs more than the defect they hide, and the retired paths they would resurrect are already covered by behaviour tests.
- **Fixed on the surface they belonged to** — the findings in the configuration page, the controller and `BackendManager` were reported rather than patched, and the other session's branch (`agent/config-page-layout@f801a37`) closed all three; the merge that brought them in resolved two comment-only conflicts by keeping both sides.

## Deliberate risks

- The server-side strict-season filter is irreversible for the snapshot: a candidate that states another season never reaches the panel, so no toggle can bring it back. Accepted because the panel's own season rule already hid those from the default view.
- Candidates that state *no* season are kept, so a whole-season pack of the wrong season can still appear under 「含整季包」. Accepted: guessing would silently drop entire seasons.
- Per-round candidate files accumulate in `/tmp` instead of being overwritten.
- **The captain's own mutation evidence was wrong twice this session** (stale `__pycache__` accepted for a same-size edit; and an earlier prototype fixture nested a dict in a list). Every "watched it go red" claim in this record was re-run with `python3 -B` and a cleared bytecode cache; anything not re-run that way is not claimed here.
