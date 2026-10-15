# Decision: Intent — how the "downloads fail" goal was cut into decisions, and in what order

English | [中文](2026-10-15-intent-download-fix-decisions.zh.md)

Status: implemented
Date: 2026-10-15
Class: process

## Problem

A person who owns this machine wanted a title that is not in the library yet: hand the name
over, get the content. What actually happened, in their words and ours, was that the system
either did nothing, or threw away the part it had already fetched — so they got neither the
content nor an error they could act on.

The cost was observable before anything was changed:

- Eight submissions were refused in **105–228 ms** each — every one of them, on a machine where
  the same code path is supposed to spend a 25-second window fetching metadata.
- With the shipped defaults, the same title returned **0 candidates**; turning one field off
  returned 43, and two other titles went 0 → 19 and 0 → 6.
- A refused task's bytes were **nowhere on disk** (a full-volume search for its release name,
  for any partial-file suffix, and for anything large written that day all came back empty),
  while the task record claimed 13.02 GB fetched and 0 bytes verified.

The goal therefore contained several decisions, not one. This record says which they were, which
class each fell in, and why the order between them is what it is — so the next reader can tell a
proposal from a bug fix instead of routing everything through the same weight.

## Decision

Six decisions, three of which were proposals and three of which are bug fixes that needed none.

**D1 — who makes the external tools available, and how the runtime finds them.** Class: `feature`.
Now: `install.sh` resolves `aria2c`/`ffprobe`/`ffmpeg`, skips any that exist, installs the missing
ones, and records the absolute paths to `<plugin dir>/backend-tools.json`; `ToolLocator.cs` resolves
in the order *configured value → record file → `PATH` → common prefixes*; `BackendManager` prepends
the tool directories to the child `PATH` and injects `JMD_ARIA2C_PATH`/`JMD_FFPROBE_PATH`/
`JMD_FFMPEG_PATH`; the backend resolves through `tool_path()` and raises `ToolMissing` (a
`RuntimeError`, deliberately not an `OSError`) instead of letting the old
`except (OSError, …): return []` turn "the tool is missing" into "this torrent has no readable
video". Delivery stages inside this one decision: detect/install/record → locator and injection →
stop swallowing → a guard that pins it.

**D2 — what the default reachability of a search is.** Class: `feature`.
Now: `publisher_only_default(kind)` is `True` for `episode` and `False` for `movie`, `record` and
`variety`; `keywords_for(kind)` keeps the three TV-release-group keywords for episodes and uses
`高清影视之家`/`HDBTHD` for film and documentary; the resolved `kind` travels to the search
subprocess (`backend/console_server.py` appends `--kind`), and the panel sends `record` when the
target library is Records.

**D3 — whether a submit has an escape hatch.** Class: `feature`.
Now: `/api/submit` accepts `trust_magnet` (default false, unchanged command line when absent), and
the panel exposes it as an off-by-default switch.

**D4 — what happens to data when a task is refused.** Class: `bug-fix`; per the intent step's table
("a slice only makes existing behaviour match what was declared") this needed no proposal.
Now: `reject_and_preserve` records and returns, touching neither the task row nor the files;
`--replace-existing` answers `REPLACE_BLOCKED` with exit code 7; `--quarantine-rejected` is the
opt-in path and it **moves** files to `<staging>/.rejected/<info_hash>/` with a `REJECTED.json`
instead of deleting them; `watch_download.py`'s slow-task abort logs `ABORT_SLOW_TASK … (task left
running; no client DB write, no file removal)`.

**D5 — what happens to the score-threshold control.** Class: `simplification`.
Now: the panel shows a read-only `≥N 分` line whose only source is the configured `MinScore`
(60); the five-option selector is gone, because the control had no consumer that could use it — its
"lower the threshold" button wrote 55, which was never one of its options.

**D6 — the defects that needed no proposal.** Class: `bug-fix`.
Now fixed: `/api/submit` and `/api/watch` resolve `year` through one shared `resolve_year` (the
payload no longer carries `""`); the season page no longer labels a season "电影"; the manual panel
follows its type choice into the target library and re-reads the snapshot from disk;
`write_task_magent` decides per `info_hash` rather than per directory; `tools/check-invariants.py`
counts every kind of failure in its summary line.

**Order.** D1 first, because it is the precondition for *any* submission succeeding; D2 and D3 are
independent of each other and were built in parallel; D4 and the D6 set were folded in as they were
found.

## Alternatives considered

**Augment `PATH` with the Homebrew directory.** This was the first P0 proposal and it lost: it
hard-codes one machine's prefix into the fix, so a MacPorts or `/usr/local` install would fail the
same way with a different directory. Installing at install time and recording resolved paths keeps
the failure mode (a missing tool) but makes the *fix* prefix-agnostic, and it lets the configuration
page report which tool is missing and what to run.

**Put a "single source" switch in the panel and leave the global default alone.** It lost to
per-kind defaults: a switch asks the person to understand a field whose meaning they cannot see,
while a type-aware default makes "I want a film" work without anyone learning the field's name. The
measured coverage decided it — the bare-title tier alone reaches 7 of 8 sampled titles, and the
single-source default is exactly what prunes that tier away.

**Give the threshold selector the missing 55 option.** It lost to removing the control: the ladder
exists to serve a comparison nobody performs, and the read-only line still shows what the gate is
actually using.

**Keep deleting on refusal, or delete after moving to a quarantine.** Both lost. Deleting is what
produced this session's report — the task stayed in the client, its bytes were gone, and the resume
complaint could not be explained from the outside. Moving-then-deleting is the same loss with an
extra step; the opt-in quarantine keeps the bytes recoverable and hands the decision back to the
person, which is the only actor who can weigh "this seed is dead" against "I still want this file".

**Bypass the client's database writes by finding a supported interface.** This was checked rather
than assumed: `plutil -p /Applications/Thunder.app/Contents/Info.plist` declares only
`thunder`, `ed2k`, `magnet`, `birdcmd`, `xunleiapp` and `thunderExtension` — every one of them is an
"add a task" scheme. There is no supported pause/delete/select interface, so the honest answer is to
write nothing and say so, rather than to keep writing the client's SQLite from behind its back.

## Consequences

Submitting now works on a machine where the media server was started the normal way (26.7 s spent in
the metadata gate, against 105–228 ms refusing immediately before), films and documentaries have
candidates under the shipped defaults, and a refused task keeps its bytes.

What is now impossible or unsupported: the plugin no longer pauses, deletes or re-selects files for
a task on its own, because the client offers no interface for it; cleanup is explicit
(`cleanup_download.py`, or `--quarantine-rejected`), and a refused task is left in the client for a
person to decide about.

Deliberately left out, each with the condition that should reopen it:

- A watcher started for a candidate that never became a task stays `status=running` with
  `taskid=None` for up to ~45 minutes. Revisit when someone again watches a monitor that never
  attaches to a task.
- Two ETA scales coexist: the submit gate's `--eta-max-hours 4` and the watcher's
  `--abort-eta-hours 6` with three strikes. Revisit at the first real report of "accepted, then
  judged too slow".
- Empty staging shells left by refused submissions are not recycled.
- The manual panel's use of an on-disk snapshot is still not discoverable from the panel itself.
- The `rc=5 PATCH_NOT_APPLIED` path is covered by guards but was **not** reproduced on the real
  machine in this session: the existing-task fast path and dead swarms intercepted every attempt.
  Revisit with a candidate whose metadata resolves and whose selection patch then fails.
- `-6009` and `errno_code=114006` in the client's own record are still unexplained; no public
  documentation and no client log on this machine.
- The standalone skill copy under `~/.agents/skills/jellyfin-movie-downloader/` carried the same
  refusal-deletes-data behaviour; it was brought in line on the same day (its own tests: 35 OK).
  It is a separate copy, not a mirror, so it will drift again — revisit when either side next
  changes a refusal path.

## Testing

- [A1] Declared surface `workflow`: the UI guard `tools/checks/check-p2-ui.mjs` executes as part of
  `python3 tools/check-invariants.py`, so the declared surface `workflow` fails when the threshold ladder
  returns, when the season subtitle falls back to "电影", or when the type→library link is removed;
  `--mutate` reports 5/5 RED-OK.
- [A2] The declared surface `tests` carries D4: `python3 -m unittest discover -s tests` reports
  `Ran 414 tests / OK (skipped=2)`, including the two guards written for it — both were observed red first
  (`拒绝后视频不许消失`, and a database fingerprint mismatch). The D1 and D4 code paths additionally carry
  their own guards under `tests/backend_probe/` and `tests/backend_download/`.
- [A3] This record is held by the declared checks `note-class` and `decision-implemented`; both are reported
  green by `python3 tools/check-invariants.py` (`54 check(s), 0 failure(s)`), and `criteria-traced` is what
  rejected the first draft of this section.
- [A4] The declared surface `evidence` carries the real-machine half: `/tmp/jmd-test/evidence/FIX-E2E/RESULT.md`
  records the install writing `backend-tools.json`, the backend's parent being the media server, three
  submissions at 26.7 s / 27.2 s, `REPLACE_BLOCKED` exiting 7, and identical directory and row fingerprints
  before and after. What did not run is stated there too: the `rc=5` path was not reproduced on the machine.
