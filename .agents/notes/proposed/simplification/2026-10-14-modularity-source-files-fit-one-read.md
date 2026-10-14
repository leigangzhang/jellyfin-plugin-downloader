# Proposal: modularity — one source file fits one read, one domain gets one directory

English | [中文](2026-10-14-modularity-source-files-fit-one-read.zh.md)

Status: proposed
Date: 2026-10-14
Class: simplification

## Problem

Whoever works in this repository must read a file whole before changing anything it holds, and a file larger than one read cannot be read whole: it comes back in slices, and each slice is a full turn that resends what the earlier ones already said. Five files were past that limit, and the cost was measured rather than assumed.

- `backend/probe_magnets.py`, 58.5 KB / 1530 lines — one worker read it in 27 slices and still covered only about 90% of it; that reading alone cost roughly four minutes of round-trips, for a file nobody had ever needed to change in one piece.
- `backend/search_pool.py` 67.3 KB / 1570 lines, `tests/test_backend_sources.py` 70.5 KB / 1430 lines, `tests/test_backend_retired_paths.py` 64.0 KB / 1208 lines, `Jellyfin.Plugin.JellyfinDownloader/Web/inject.js` 79.6 KB / 1874 lines — the same shape waited for every reader of every one of them.
- The files were not comment-heavy: `probe_magnets.py` was 4% comments, so there was nothing to delete. The mass was live code that had never been grouped, and two of the five sat under any line count a reviewer would defend while still being over the byte limit.

## Proposal

State the limit in the unit it is actually measured in, and treat crossing it as a placement decision rather than a trimming one: a source file that a reader must open whole may not exceed the reader's own ceiling, and past the 30 KB warning band it is a candidate for the next split. When a file crosses it, the fix is to separate responsibilities — never to cut at a line number — and responsibilities that belong to one domain then live in one directory, so the next reader searches a domain instead of a flat tree.

Applied to this repository, in order:

1. Declare the ceiling as a gate: `source-budget` measures UTF-8 bytes over `backend/*.py`, `tests/*.py` and `Jellyfin.Plugin.JellyfinDownloader/Web/*.js`, at 51200 B.
2. Split the five files that were over it, one file per commit, moving code and extracting functions only — no signature, CLI, output or exit-code change.
3. Group the results by domain: `backend/probe/`, `backend/search/`, `backend/pan/`, `backend/download/`, and `tests/backend_probe/`, `tests/backend_sources/`, `tests/backend_retired/`, `tests/kit/`.
4. Keep the three files whose location is itself a contract at the root of `backend/`: `console_server.py` (the plugin starts it by that exact path), `build_console.py` (its directory is both the script-lookup root and the import root) and `media_download_lib.py` (its directory is the runtime data root).

## Alternatives considered

**Cut at a line number instead of at a responsibility.** It is the only way to hit an exact size, and it was on the table from the first request, phrased as "按行数机械切". It lost because a cut through a function body changes behaviour, and behaviour was the one thing that could not change: every criterion here is stated against the un-split tree. Where a boundary could not be drawn by responsibility — one function in `inject.js` is 212 lines and cannot be split without changing the bytes the browser receives — the file was left over the function rule and the exception recorded, rather than cut.

**Split the files and leave them flat.** This is what the first round did, and it was honest: the files were under the limit, the suite was green, and nothing was broken. It lost on the second reading. Twenty-eight scripts in one directory means a reader hunting the probe chain searches the whole tree, and a directory nobody reads is where the next oversized file grows back. Grouping cost one more move plus one contract: an entry script launched by path must still find the root it imports from.

**Delete instead of split.** Nothing in these files was dead. A census over the six modules of the first round found no unreferenced top-level name, no write-only variable, no unreachable statement and no commented-out code, and the guards that would have caught those were already green. Deletion was never available, so it is recorded as considered and unavailable rather than as rejected on merit.

## Acceptance criteria

- [M1] `source-budget` measures UTF-8 bytes over the three pattern groups and fails on any file above 51200 B; the tree passes today, with its largest source file at 43364 B and the five targets gone.
- [M2] `tests` — `python3 -m unittest discover -s tests` is green, with the same case names as before the split and no case deleted, skipped or weakened.
- [M3] `source` — `python3 -m compileall -q backend` and `./build.sh` pass, and every entry script the launcher names still runs when invoked through the console's own launcher: eight of eight return rc=0.
- [M4] `packaging` — the script the built DLL serves is byte-identical to the pre-split file (`sha256 b8feedab…c206b6`, 81462 B), so the one target that is delivered rather than imported is proven rather than asserted.

## Risks

**Revisit when a check's scan surface shrinks without anything going red.** This decision moved 22 modules and 31 test modules, and three guards silently lost coverage doing it: a stdlib-only guard kept three hard-coded filenames after `search_pool.py` became seven (68% of the bytes outside it), a retired-symbol scan globbed one level, and the syntax gate was a shell glob that reached the root directory only. All three were repaired here, but whether a guard must derive its surface from what it governs — and carry a floor against collapse — is a separate decision with its own rejected alternative, so it is not settled by this record.

**Revisit when a function, not a file, is the thing over the limit.** The 200-line rule and the byte ceiling can conflict: `openPanel` in `inject.js` is 212 lines and cannot be split without changing the served bytes, which this decision requires to stay identical. It is a known exception that needs its own round with visual verification, not a clause here.

**Revisit when the ceiling is raised.** 51200 B is the reader's own limit, not a preference. The number moves when the reader's limit moves, and never to admit a file that is cheaper for the tree to edit unsplit.

**Revisit when a module name repeats its package.** `probe/probe_health.py` keeps the old flat name so that every reference, docstring and test keeps meaning what it meant; if a reader starts confusing the two, renaming inside the package is a separate decision.

**The gate measures bytes, not readability.** A 40 KB file of dense logic passes and still costs a long read; that is what the 30 KB warning band is for, and it is deliberately not a gate. A second gate in the band would red on legitimate files and be deleted within a month.

**Grouping is not free for shared code.** A module that many domains import cannot move into any one of them without becoming a cross-domain dependency; the ones that stayed at the root of `backend/` stayed for that reason, and the next shared module must be judged the same way rather than by size.
