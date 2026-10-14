# Decision: modularity — one source file fits one read, one domain gets one directory

English | [中文](2026-10-14-modularity-source-files-fit-one-read.zh.md)

Status: implemented
Date: 2026-10-14
Class: simplification

## Problem

Whoever works in this repository must read a file whole before changing anything it holds, and a file larger than one read cannot be read whole: it comes back in slices, and each slice is a full turn that resends what the earlier ones already said. Eleven files were past the 30 KB warning band and five of them were past the reader's own ceiling, and the cost was measured rather than assumed.

- `backend/probe_magnets.py`, 58.5 KB / 1530 lines — one worker read it in 27 slices and still covered only about 90% of it; that reading alone cost roughly four minutes of round-trips, for a file nobody had ever needed to change in one piece.
- `backend/search_pool.py` 67.3 KB / 1570 lines, `tests/test_backend_sources.py` 70.5 KB / 1430 lines, `tests/test_backend_retired_paths.py` 64.0 KB / 1208 lines, `Jellyfin.Plugin.JellyfinDownloader/Web/inject.js` 79.6 KB / 1874 lines — the same shape waited for every reader of every one of them.
- The files were not comment-heavy: `probe_magnets.py` was 4% comments, so there was nothing to delete. The mass was live code that had never been grouped, and two of the five sat under any line count a reviewer would defend while still being over the byte limit.
- The second half of the work was the six files that sat **between** the 30 KB band and the 51200 B gate, so the gate never fired for any of them: `backend/console_server.py` 40282 B / 932 lines, `backend/media_download_lib.py` 39758 B / 1218, `backend/candidate_score.py` 31681 B / 868, `tests/backend_probe/test_backend_probe.py` 43364 B / 924, `tests/backend_probe/test_backend_integration.py` 32467 B / 643, `tools/pair-docs.py` 32883 B / 758. The band is where a slice-by-slice read lives on, and three of the six carried the other half of the same cost as well: functions nested five to seven levels deep (`_run_search_job` in `console_server.py` at 6, five functions in `media_download_lib.py` at 5–7, `sections` in `pair-docs.py` at 5), so the file was not the only unit a reader had to hold whole.

## Decision

The limit is stated in the unit it is actually measured in, and crossing it is a placement decision rather than a trimming one: a source file a reader must open whole may not exceed 51200 B, past the 30 KB band it is a candidate for the next split, and a file that crosses the ceiling is separated by responsibility — never cut at a line number — into a directory that names one domain. `source-budget` declares the ceiling over `backend/*.py`, `tests/*.py` and `Jellyfin.Plugin.JellyfinDownloader/Web/*.js`.

Both halves are built. The first moved `backend/probe_magnets.py`, `backend/search_pool.py`, `tests/test_backend_sources.py`, `tests/test_backend_retired_paths.py` and `Web/inject.js` into `backend/probe/`, `backend/search/`, `backend/pan/`, `backend/download/`, `tests/backend_probe/`, `tests/backend_sources/`, `tests/backend_retired/`, `tests/kit/` and the five `Web/inject_0*.js` shards. The second moved the six files above into directories of their own:

- `backend/console_server.py` keeps its path and now holds the contract surface and the composition root; the implementations are in `backend/console_server/{__init__,http_api,jobs,probe_state,service}.py`.
- `backend/media_download_lib.py` is gone. The flat import `media_download_lib` still resolves, now to `backend/media_download_lib/{__init__,paths,db,torrents,cleanup,media,finalize,watcher,_shared}.py`, whose `__init__.py` re-exports all 37 names callers import.
- `backend/candidate_score.py` is a 27-line entry that bootstraps `sys.path`, re-exports and forwards `__main__`; the code is in `backend/candidate_score/{__init__,policy,rules,release,speed,score,cli}.py`.
- `tests/backend_probe/test_backend_probe.py` and `tests/backend_probe/test_backend_integration.py` are gone. Their cases are in six `test_probe_*.py` and four `test_backend_*.py` siblings, with the shared fixtures in `_backend_probe_helpers.py` and `_backend_integration_helpers.py`.
- `tools/pair-docs.py` is a 32-line entry; the code is in `tools/pairdocs/{__init__,model,render,checks,cli}.py`.

Every name that a contract addresses by path kept its path. `BackendManager.cs:99` starts `<plugin>/backend/console_server.py`; `uninstall.sh` recognises the process by the name `console_server.py` in eight `pgrep -f` calls; `tests/backend_probe/test_backend_scoring.py:28` loads `backend/candidate_score.py` through `spec_from_file_location` with `backend/` off `sys.path`; `tools/kitcheck/links.py:24` loads `tools/pair-docs.py` the same way under the module name `pair_docs`.

Three mechanisms are what the layout forced, and each is load-bearing:

- `backend/console_server/__init__.py` checks whether the entry file is already running as `__main__`, and if it is, aliases `sys.modules["console_server"]` to it instead of executing it a second time; otherwise it loads the entry by path. Without that branch the script route and the import route each define their own `Handler`, `main` and `_search_status`. Commits `7315e39` and `b0d7abf`.
- `backend/media_download_lib/paths.py` computes `DATA_ROOT` from the package's parent rather than from `dirname(__file__)`, so the data root is still `backend/state`.
- Both split test files moved their shared fixtures into an `_`-prefixed helper, because `unittest` collects any `TestCase` subclass a module imports.

The largest file under the gate is now `backend/search/search_pool.py` at 25883 B, ahead of the largest shard, `Web/inject_05_render.js` at 24565 B; no tracked `.py` is over 30720 B.

## Alternatives considered

**Cut at a line number instead of at a responsibility.** It is the only way to hit an exact size, and it was on the table from the first request, phrased as "按行数机械切". It lost because a cut through a function body changes behaviour, and behaviour was the one thing that could not change: every criterion here is stated against the un-split tree. Where a boundary could not be drawn by responsibility — one function in `inject.js` is 212 lines and cannot be split without changing the bytes the browser receives — the file was left over the function rule and the exception recorded, rather than cut.

**Split the files and leave them flat.** This is what the first round did, and it was honest: the files were under the limit, the suite was green, and nothing was broken. It lost on the second reading. Twenty-eight scripts in one directory means a reader hunting the probe chain searches the whole tree, and a directory nobody reads is where the next oversized file grows back. Grouping cost one more move plus one contract: an entry script launched by path must still find the root it imports from.

**Delete instead of split.** Nothing in these files was dead. A census over the six modules of the first round found no unreferenced top-level name, no write-only variable, no unreachable statement and no commented-out code, and the guards that would have caught those were already green. Deletion was never available, so it is recorded as considered and unavailable rather than as rejected on merit.

**A thin re-export entry for `console_server.py`.** The plan said the entry would only re-export and forward `__main__`; it lost on the tests. Five assertions read `backend/console_server.py` as text and pin literals inside it — `SEARCH_TIMEOUT = 900`, `PROBE_BUDGET_SECS = 720`, `"--budget-secs"`, the six status tokens, `return "best_dead_swarm"`, `os.environ.get("JMD_PORT")` and `{**obj, "service": SERVICE_ID}` — so the entry had to keep the contract surface and the composition root, and only the implementations moved out.

**A re-export aggregator for the two split test files.** It lost because `unittest` treats a `TestCase` subclass that a module imports as a case of that module: an aggregator re-exporting the moved classes would report every case twice (315 → about 630) and break the criterion that no case may be added. The cases became siblings and the shared fixtures moved to an `_`-prefixed helper that discovery does not collect.

**Naming the tools package `tools/pair_docs`.** `tools/kitcheck/links.py:11` sets `_PAIR_DOCS = "pair_docs"` and registers the path-loaded entry under that name in `sys.modules`; a package of the same name would make the entry import the half-initialised module instead of itself. The package is `tools/pairdocs`.

**A nested same-named package for the two test files.** `a241dab` had already grouped `tests/` by the module under test, and both files already lived in `tests/backend_probe/`, which `unittest discover` recurses into; sibling modules keep that grouping, where a fresh `tests/test_backend_probe/` directory would have undone it.

**Leaving `backend/console_server/` without an `__init__.py`.** Recorded while the first half of this work was built: `import console_server` would then resolve to the entry file and no aliasing would be needed at all. It lost because this decision puts a domain's modules in a package directory, and without an `__init__.py` that directory is not one: the modules would have to enter `backend/`'s import namespace under generic top-level names — `jobs`, `service`, `http_api` — which is the flat tree the first half set out to remove.

## Consequences

The band is empty of files. No tracked `.py` is over 30720 B, the largest gated file is `backend/search/search_pool.py` at 25883 B, and the six targets' 220435 B across six files became 41 files whose largest member is `backend/console_server/http_api.py` at 17609 B. A reader who opens any of the eleven targets now opens one file.

**The name collision is the price of the layout.** `backend/console_server.py` and `backend/console_server/` share a name because two contracts each pin one: the plugin starts the file by that exact path, and this decision puts a domain's modules in a directory of that name. Python resolves the directory first, so `backend/console_server/__init__.py` has to redirect `sys.modules` to the entry when the entry is already `__main__`. No other target needed that, and it is the one place in this refactor where a reader must know a mechanism before touching the file.

**Re-export surfaces are now a contract.** `backend/media_download_lib/__init__.py` re-exports the 37 names its callers import, and `backend/candidate_score/__init__.py` re-exports 54 names including the underscore-private ones the tests reach for. A rename inside either package now has to keep the flat name alive or update every caller in the same change.

**Revisit when a check's scan surface shrinks without anything going red.** This decision moved 22 backend modules and 31 test modules, and three guards silently lost coverage doing it: a stdlib-only guard kept three hard-coded filenames after `search_pool.py` became seven (68% of the bytes outside it), a retired-symbol scan globbed one level, and the syntax gate was a shell glob that reached the root directory only. All three were repaired here, but whether a guard must derive its surface from what it governs — and carry a floor against collapse — is a separate decision with its own rejected alternative, so it is not settled by this record.

**Revisit when source outside the gate's three groups grows.** An earlier draft here claimed `source-budget` matched one level and therefore missed every package this decision created; that was wrong, and a check disproves it. Patterns are matched with `fnmatch`, whose `*` crosses `/`, so the gate measures 104 files at any depth — planting a 60011 B file in `backend/console_server/` fails it by name. The real gap is narrower: source outside the three groups is unmeasured rather than passing, and `tools/*.py` is the largest such body, at `tools/run-evidence.py`, 22344 B. Widening the patterns to cover it is a change to the `gates` surface, deliberately not taken here.

**Revisit when a function, not a file, is the thing over the limit.** The 200-line rule and the byte ceiling can conflict: `openPanel` in `inject.js` is 212 lines and cannot be split without changing the served bytes, which this decision requires to stay identical. It is a known exception that needs its own round with visual verification, not a clause here. The same rule is still unmet elsewhere: 25 tracked `.py` files nest past four levels — `collect` in `backend/search/search_pool.py` at 189 lines / 7 levels and `main` in `backend/download/submit_xunlei.py` at 418 lines / 6 levels among them — and no check measures nesting.

**Revisit when the ceiling is raised.** 51200 B is the reader's own limit, not a preference. The number moves when the reader's limit moves, and never to admit a file that is cheaper for the tree to edit unsplit.

**Revisit when a module name repeats its package.** `probe/probe_health.py` keeps the old flat name so that every reference, docstring and test keeps meaning what it meant; if a reader starts confusing the two, renaming inside the package is a separate decision.

**The gate measures bytes, not readability.** A 40 KB file of dense logic passes and still costs a long read; that is what the 30 KB warning band is for, and it is deliberately not a gate. A second gate in the band would red on legitimate files and be deleted within a month.

**Grouping is not free for shared code.** A module that many domains import cannot move into any one of them without becoming a cross-domain dependency; the ones that stayed at the root of `backend/` stayed for that reason, and the next shared module must be judged the same way rather than by size.

**Unverified.** `install.sh` was not run for this change, because it stops Jellyfin before copying; nothing here proves the plugin's copied `backend/` tree runs in place. `JMD_DATA_DIR` is covered end to end, but only from a working tree, not from an installed plugin directory.

## Testing

- [M1] `source-budget` measures UTF-8 bytes over the three pattern groups — 104 files, at any depth — and fails on any file above 51200 B; the tree passes today, with its largest gated file at 25883 B (`backend/search/search_pool.py`), and the eleven targets gone.
- [M2] `tests` — `python3 -m unittest discover -s tests` is green, with the same case names as before the split and no case deleted, skipped or weakened.
- [M3] `source` — `python3 -m compileall -q backend` and `./build.sh` pass, and every entry script the launcher names still runs when invoked through the console's own launcher: eight of eight return rc=0.
- [M4] `packaging` — the script the built DLL serves is byte-identical to the pre-split file (`sha256 b8feedab…c206b6`, 81462 B), so the one target that is delivered rather than imported is proven rather than asserted.
- [M5] `tests` — the two split test files keep their case identifiers: the `Class.method` multiset reported by `python3 -m unittest discover -s tests` is identical before and after, and the run still reports `Ran 315 tests`. That count is what makes the alternative of a re-export aggregator a failure rather than a style choice.
- [M6] `source` — every file in the six targets' new homes is ≤30720 B: 38 new files plus the three retained entries, largest `backend/console_server/http_api.py` at 17609 B. **unpinned**: nothing measures the 30 KB band — `source-budget` fires only above 51200 B — and nothing measures nesting at all, so this was verified by an ad-hoc AST scan that no gate repeats.
- [M7] `source` — the entry contracts still hold: `python3 backend/console_server.py --port <free>` answers `GET /api/snapshot` with `"service": "jellyfin-downloader-backend"`; `python3 -c "import sys; sys.path.insert(0, 'backend'); import console_server"` exposes one module object, starts no service and reads no state; `python3 backend/candidate_score.py --help` exits 0; `python3 tools/pair-docs.py --check` reports every pair in step. The manual Jellyfin smoke path on the `source` surface is what owns the first of these over time.
- [M8] `packaging` — the plugin ships `backend/` as a tree, so `backend/console_server/`, `backend/media_download_lib/` and `backend/candidate_score/` travel with the DLL and `install.sh`'s `cp -R backend` carries them. **unverified**: `install.sh` itself was not run, so no run proves the copied tree works in place.
