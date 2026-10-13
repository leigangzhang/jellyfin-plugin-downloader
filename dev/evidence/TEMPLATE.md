# Evidence record

Recorded: <the UTC instant the run finished>
Scope: <the verified base, or "worktree only">
Verdict: PASS — 0 passed, 0 failed, 0 could not run (of 0 runnable command(s))

## Changed paths and their surfaces

This file is the skeleton of what `python3 tools/run-evidence.py --base <ref> --record`
writes. The tool writes one record per delivery to `dev/evidence/<slug>-<merge-base>.md`
and never edits this one. It exists so the `evidence-record` check has a subject in a repository whose own run
records have been deleted, and it is a faithful record of a run that had nothing to run.

### <surface name>

<the surface's own `why`, copied by the writer>

- `<changed path>`

Then one entry per runnable command, written by the tool as a bold state, an em dash, and
the command in backticks. The state is one of `PASS`, `FAIL`, `UNKNOWN`, `UNFILLED`, or
`manual`; only the first four are counted in the line above, because a manual entry is
what a person still has to do or judge.

## Paths with no declared surface

Written only when some changed path matched no surface, so a reader can see what the
change touched that nothing yet owns.
