# Proposal: gates — a check's scan surface follows what it guards, and a shrunken surface goes red

English | [中文](2026-10-14-gates-scan-surface-follows-what-it-guards.zh.md)

Status: proposed
Date: 2026-10-14
Class: process

## Problem

Whoever maintains this repository cannot tell, when a structural refactor finishes, whether the checks still cover what they covered before. The only way to find out today is to re-read every guard by hand — which is the reading the refactor was supposed to remove.

Three moves in one refactor each shrank a guard's scan surface, and none of the three was caught by a check:

- `DependencyTests` in `tests/backend_sources/test_backend_sources_dependencies.py` kept enumerating `search_pool.py`, `torrents_csv_source.py` and `source_config.py` after `search_pool.py` became seven files. 53722 of the original 79021 bytes — 68% — sat outside the stdlib-only guard, so `import requests` written into `backend/search/search_pool_api.py` was green.
- `backend_modules()` in `tests/backend_retired/_backend_retired_helpers.py` globbed one level; after 22 modules moved under `backend/{probe,search,pan,download}/`, the retired-symbol scan read 6 files instead of 28.
- The syntax evidence on the `source` surface was `python3 -m py_compile backend/*.py`, a shell glob that reaches the root directory only, so the same 22 files dropped out of it.

The cost is not the three repairs; each was a few lines. It is that all three were found by a person reading the guard afterwards, and that the same shape recurred three times inside a single refactor. A guard that quietly stops guarding reports green, and green is read as "covered" — the failure is invisible exactly where a reader looks for assurance.

## Proposal

Two rules for every guard that scans a set of files rather than one named file.

1. **The surface is derived, not listed.** The guard computes what it scans from the object it governs — a recursive walk of the directory it owns, a declared manifest, or the set of modules it asserts about. Naming three files next to a directory that holds twenty-eight is the defect, not a style preference.
2. **A surface has a floor.** The guard asserts the surface it just derived is at least as large as it was when the guard was written, so a glob that stops matching, a directory that is renamed, or files that move out from under the guard turns red instead of passing vacuously.

Applied to this repository, the change names these files:

- `tools/workflow.json`, `surfaces[source].evidence` — already `python3 -m compileall -q backend`, which walks the tree; keep it recursive and keep the comment that says why.
- `tests/backend_sources/test_backend_sources_dependencies.py` — already `BACKEND.rglob("*.py")` with package directory names allowed, plus the `>= 10` floor.
- `tests/backend_retired/_backend_retired_helpers.py::backend_modules()` — walks recursively today and has no floor; this is the one that needs adding.
- `tests/backend_retired/test_backend_retired_paths.py` — iterates a scan surface and ends in `assertEqual(offenders, [])`, so an empty surface passes today.
- `tests/backend_retired/test_backend_retired_backend_contract.py` — the same shape at the same place (`offenders`), with the same missing precondition.
- `tests/AGENTS.md` — gains the two rules as a short paragraph beside the existing "grouped by subject" note, so the next guard is written this way instead of re-deriving the policy from the last incident.

No new check kind and no new tool: a floor is an ordinary assertion, and the assertion is the check that goes red.

## Alternatives considered

**A new `scan-surface` check kind in `tools/check-invariants.py`.** It would put the rule in one place instead of in each guard, which is genuinely attractive. It lost on two counts: the engine's kinds are a closed set with their own self-tests, and a recursive-walk kind would still have to decide, per guard, what a "surface" is — the guard already knows, and making it declare that in `workflow.json` is a second fact that can drift. Reconsider if a fourth occurrence appears, or if the floors start being deleted one by one.

**Leave it to the review round.** This is what happened, and it is why the proposal exists: the 68% gap was found by a reviewer, after the work was merged and reported. A reviewer is the right place to catch a wrong decision, not the only place that catches a guard which no longer scans anything. It lost because the reader who has to notice is the same reader the rule is meant to relieve.

**Keep listing paths, and add each split module to the list by hand.** The cheapest option, and the de facto policy before this refactor. It lost on evidence: under it, three lists went stale inside one refactor, and a maintainer reading a list cannot tell a complete one from a stale one.

## Acceptance criteria

- [G1] `source` — moving a module into a new directory under `backend/` leaves both commands (`python3 -m compileall -q backend`, `./build.sh`) untouched and still covering it, and moving that module out of `backend/` makes at least one of them fail.
- [G2] `tests` — shrinking any guarded surface to the root level (`glob` in place of `rglob`, or an empty offenders set) fails at least one case, so a collapsed surface is red rather than vacuously green.
- [G3] `workflow` — `python3 tools/check-invariants.py --self-test` still passes after the rules land, so the declarations were not broken to satisfy the change.

## Risks

**Revisit when the move-equivalence script has to be written a third time.** Proving a refactor did not change behaviour — comparing the moved definitions token-for-token, plus looking for unreachable and duplicated statements — is currently a scratch script under `/tmp`, written twice in this refactor and thrown away twice; the second version was missing the unreachable check, and a human walkthrough found the gap. That is a separate decision with its own rejected alternative (keep it a scratch script), so it is not decided here.

**Revisit when the 30 KB warning line starts cutting files that belong together.** The thresholds themselves are a separate decision, and no harm has been observed from them yet.

**Revisit when `probe/probe_health.py`-style prefixes start causing ambiguity.** Whether a module keeps its full name inside its package is a naming decision; it defers no capability and has no consumer asking for it today.

**Revisit when `openPanel` next needs editing.** One function exceeds the 200-line rule and cannot be split without changing the bytes the browser receives, which the split was required to preserve. It is a known exception that needs its own round with visual verification, not a clause in this rule.

**The recursive surface can over-reach.** A walk of `backend/` would also pick up generated or vendored files if any appear there; the floors must not be raised to accommodate that noise — exclusions belong in the guard, next to the reason.

**A floor is a number that can be wrong.** If modules are legitimately merged, a floor sized to the old count fails a correct tree. A floor is a lower bound against collapse, not a census; when a guard's subject genuinely shrinks, the floor moves with it in the same change, and the commit says so.
