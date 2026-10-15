"""Executable conventions: every check here can fail, and --self-test proves it.

Checks are declared in tools/workflow.json. Twelve kinds are supported:

  budget                files matching `patterns` stay at or under `maxLines`, `maxWords`
                        (whitespace-separated, as `wc -w` counts them), or both
  required-sections     files matching `patterns` contain every section in `sections`,
                        and each section body has at least `minBodyLines` non-blank lines;
                        an entry may list the accepted spellings of one section, so a
                        page that translates its headings declares its own wording
  forbidden-regex       files matching `patterns` contain no `regex` match
  note-class            files matching `patterns` are records at
                        .agents/notes/<lifecycle>/<class>/<file>; the class is one of `classes`,
                        the lifecycle is one of `lifecycles`, and the file's `Class:` line
                        agrees with its folder, so a record cannot be filed under the
                        wrong class and drift away from the taxonomy
  source-mirror         fenced blocks registered in `manifest` equal the source region
                        between a `begin` and an `end` marker line; trailing whitespace
                        and blank edges are ignored, nothing else is
  link-target           files matching `patterns` carry no relative link that names no
                        file; a link into a stage the tier switch declares absent is
                        exempt, and the scanner is the one tools/pair-docs.py uses
  evidence-record       files matching `patterns` are records written by
                        tools/run-evidence.py: each carries the shape a reader needs, and
                        its `Verdict:` counts agree with the entries below it
  capability-registry   capabilities registered in `manifest` are complete, and their
                        keys equal the `capability: <key>` markers discovered in source
  criteria-traced       every bullet in the listed `sections` carries an id and cites a
                        check id or surface name read from `declaredIn`, so no criterion
                        passes without an owner that is configured to go red
  skill-trigger         a skill's front-matter `description` opens with when the skill
                        loads, not with a summary of the skill's own contents
  publish-manifest      every file matching `patterns` is declared exactly once as
                        `public` or `internal` in `manifest`; each `public` entry names
                        one file that exists, and every `internal` entry states why
  sealed-manifest       every file matching `patterns` except the manifest itself is
                        registered exactly once in `manifest` with the SHA-256 of its
                        text, its archive date, and a reason; the file must still hash to
                        that value and must declare `Archived: <that date>`

Patterns use fnmatch semantics, in which `*` also matches `/`, so "*.md" covers every
Markdown file at any depth and "AGENTS.md" covers only the root file.

A `source-mirror` manifest is a JSON object with a non-empty `mirrors` list. Each entry
names `doc`, `fence`, `source`, `begin`, and `end`; blocks and entries are one-to-one, a
duplicate `(doc, fence)` is a violation, and a `doc` outside the check's `patterns` is
reported because it would never be scanned. The check exists because a pasted contract
nobody re-checks silently becomes a second, wrong fact.

A `capability-registry` manifest is a JSON object with a non-empty `capabilities` list.
Each entry names `key`, `kind`, `definition`, and `note`; a `seam` also needs at least
one `providers` and one `consumers` path, and every path must exist. With `discover`
configured, the registered keys and the keys declared under `discover.patterns` must be
exactly equal in both directions: a declaration is a line whose first non-space text is
`discover.marker` followed by the key, so the marker string normally carries the
comment syntax (`# capability:`), and prose that merely mentions the marker does not
count. The check exists because a capability that nobody classifies, or a
classification whose subject is gone, is invisible either way.

A `publish-manifest` manifest is a JSON object with a non-empty `public` list and an
`internal` list. Every file matching the check's `patterns` must match exactly one
declared entry: a file nobody declares is a silent omission rather than an unpublished
document, and a file declared twice is an ambiguity. A `public` entry names one file and
may not contain glob magic, because publication is a per-file promise; it must also
match a file, since a promise with nothing behind it is a broken link. An `internal`
entry may name a home and may match none, because an empty shelf is not a promise, but
it must say why the file stays. The check exists because "the file exists" and "the file
ships" are different facts, and only the manifest can tell them apart.

A `sealed-manifest` manifest is a JSON object with a `sealed` list, which may be empty:
an archive is append-only, so it starts with nothing in it, and the guard is not vacuous
because every file matching the check's `patterns` — except the manifest itself, which
cannot seal itself — must be registered. Each entry names `path`, `sha256`, `archived`,
and `reason`; `sha256` is the digest of the file's UTF-8 text with universal newlines, so
the seal freezes content rather than the line endings of the machine that wrote it. Every
entry must fall inside the check's `patterns`, name a file that exists, hash to the
recorded value, and agree with an `Archived: <date>` line in the file itself. The check
exists because an archived record is kept for a reason, and a record that can still be
edited is a record that will contradict the reason it was kept for.

A `criteria-traced` check turns every bullet under its `sections` into a claim with an
owner. Each bullet carries an id matching `idPattern` (default `[A1]`, `[AC-2]`) and
cites at least one name in backticks that is declared in `declaredIn` as a `checks[].id`
or a `surfaces[].name`. Backticked text that is not a bare lowercase name — a command, a
path, a sentence — is free text and is not resolved, because no check can run it. A
section with no bullets is rejected too: a heading that carries nothing cannot be
traced. The check exists because a criterion nobody owns is a wish, and the owner it
names is exactly the thing that will go red when the criterion breaks.

One check is deliberately not declared in tools/workflow.json: the P2 UI behaviour guard
(tools/checks/check-p2-ui.mjs, driven by node, because it runs the panel's own code rather
than scanning text). No declarative kind above can express "concatenate the five Web shards
in the controller's ScriptSegments order, run them against a DOM stub, and assert the
payload the panel really posts". This script therefore runs it as a built-in check right
after the declared ones, under the id `ui-p2-guard`, and its failure fails this command like
any other check:

  python3 tools/check-invariants.py --self-test   also runs the guard's `--mutate` mode,
                                                  which reverts three fixed UI behaviours one
                                                  at a time and requires each revert to turn
                                                  the guard red, so the guard is inside the
                                                  same "every check can fail" proof as the
                                                  declared kinds
  --ui-web-dir DIR                                points the guard at another copy of the
  JDL_WEB_DIR environment variable                shards, which is how a mutation is
                                                  rehearsed without editing the repository

`--list` does not run it (the list is the declared set), and `--changed BASE` runs it only
when the changed paths touch the Web shards, the guard, or this script.

Output contract: a run prints exactly one summary line, and it is the last line of the run —
`check-invariants: <n> check(s), <m> failure(s)`, where `n` counts the declared checks that
ran plus the built-in guard, and `m` counts the checks that failed. The line and the exit
code come from the same results: exit is 1 iff `m > 0`. A guard that is out of scope prints
`SKIP` and enters neither number. A run that reports `0 failure(s)` while the process exits
1 is the defect this line exists to prevent: the guard's own internal line used to sit last
and read as the run's summary (it now carries the `[ui-p2-guard]` prefix), and the engine's
count could not see a failing `note-class`. If the two sources disagree, this script refuses
to print the summary it cannot back and exits 2.

Exit codes:
  0  every check passed, or --self-test passed
  1  at least one violation, or a self-test failed
  2  usage or environment error (bad config, unsupported check kind, invalid regex, node
     missing while the UI guard is in scope)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import importlib.util as _importlib_util
from pathlib import Path as _Path

_PACKAGE = _Path(__file__).resolve().parent / "kitcheck"
_SPEC = _importlib_util.spec_from_file_location("kitcheck", _PACKAGE / "__init__.py",
                                               submodule_search_locations=[str(_PACKAGE)])
if _SPEC is None or _SPEC.loader is None:                      # pragma: no cover - packaging error
    raise SystemExit("tools/kitcheck is missing")
_ENGINE = _importlib_util.module_from_spec(_SPEC)
sys.modules["kitcheck"] = _ENGINE
_SPEC.loader.exec_module(_ENGINE)

_TOOLS = _Path(__file__).resolve().parent
_ROOT = _TOOLS.parent
_UI_GUARD = _TOOLS / "checks" / "check-p2-ui.mjs"
_UI_GUARD_ID = "ui-p2-guard"

# The guard reads the shards it protects, so iteration mode runs it only for a change that
# can move them — or a change to the guard or the wiring itself, which is how a mistake in
# either is caught before the delivery-boundary run.
_UI_GUARD_SCOPE = (
    "Jellyfin.Plugin.JellyfinDownloader/Web/",
    "tools/checks/",
    "tools/check-invariants.py",
)
_NODE_TIMEOUT_SECS = 120


def _consume_web_dir(argv: list[str]) -> tuple[list[str], str | None]:
    """Pull `--ui-web-dir DIR` out of argv; it belongs to the guard, not to the engine."""
    rest: list[str] = []
    web_dir: str | None = None
    index = 0
    while index < len(argv):
        if argv[index] == "--ui-web-dir":
            if index + 1 >= len(argv):
                print("check-invariants: --ui-web-dir needs a directory", file=sys.stderr)
                raise SystemExit(2)
            web_dir = argv[index + 1]
            index += 2
            continue
        rest.append(argv[index])
        index += 1
    return rest, web_dir or os.environ.get("JDL_WEB_DIR")


def _changed_paths(argv: list[str]) -> list[str] | None:
    """Paths `--changed` scoped this run to, or None when the scope cannot be read."""
    if "--changed" not in argv:
        return None
    base = argv[argv.index("--changed") + 1: argv.index("--changed") + 2]
    if not base:
        return None
    try:
        paths, _merge_base = _ENGINE.core.changed_paths(
            base[0], worktree_only="--worktree-only" in argv, cwd=_ROOT, prog=_UI_GUARD_ID)
    except SystemExit:
        raise
    except Exception:                                          # pragma: no cover - fail-safe
        return None
    return paths


def _ui_guard_in_scope(argv: list[str]) -> bool:
    paths = _changed_paths(argv)
    if paths is None:
        return True                                            # full run, or scope unknown
    return any(path.startswith(scope) for path in paths for scope in _UI_GUARD_SCOPE)


def run_ui_guard(*, mutate: bool, web_dir: str | None) -> int:
    """Run the P2 UI behaviour guard and report it as one more check.

    A guard that cannot start is an environment error, not silence: a check that did not
    run has not passed, and `node` missing while the shards are in scope is exactly the
    misconfiguration this repository wants loud.
    """
    node = shutil.which("node")
    if node is None:
        print(f"FAIL {_UI_GUARD_ID}")
        print("  node not found on PATH; the UI guard cannot run, so it has not passed")
        return 2
    if not _UI_GUARD.is_file():
        print(f"FAIL {_UI_GUARD_ID}")
        print(f"  {_UI_GUARD.relative_to(_ROOT)} is missing")
        return 2

    argv = [node, str(_UI_GUARD)]
    if mutate:
        argv.append("--mutate")
    if web_dir:
        argv += ["--web-dir", web_dir]

    try:
        completed = subprocess.run(argv, cwd=_ROOT, capture_output=True, text=True,
                                   timeout=_NODE_TIMEOUT_SECS, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"FAIL {_UI_GUARD_ID}")
        print(f"  the guard could not be started: {error}")
        return 2

    detail = (completed.stdout + completed.stderr).strip().splitlines()
    if completed.returncode == 0:
        print(f"PASS {_UI_GUARD_ID} ({'mutation self-test' if mutate else 'Web shards'})")
        for line in detail:
            print(f"  [{_UI_GUARD_ID}] {line}")
        return 0
    print(f"FAIL {_UI_GUARD_ID}")
    for line in detail:
        print(f"  [{_UI_GUARD_ID}] {line}")
    return 1


def main(argv: list[str]) -> int:
    argv, web_dir = _consume_web_dir(list(argv))

    if "--list" in argv:                                       # the list is the declared set
        return _ENGINE.main(argv)

    # The engine reports its own counts instead of printing a summary, so the line this
    # script prints last is the only summary and it covers every check that ran — the
    # declared ones and the guard below. A run that ends with "0 failures" while the
    # process exits 1 is the defect this shape exists to prevent.
    summary: dict[str, int] = {}
    engine_rc = _ENGINE.main(argv, summary=summary)

    if "--self-test" in argv:
        # The engine's self-test proves every declared kind rejects an invalid fixture and
        # that the summary counter counts every failing kind; the guard proves the same
        # about itself by reverting the behaviours it protects.
        guard_rc = run_ui_guard(mutate=True, web_dir=web_dir)
        groups = (("declared checks + summary counter", engine_rc), (_UI_GUARD_ID, guard_rc))
        failures = sum(1 for _name, code in groups if code)
        print(f"check-invariants: self-test {'FAILED' if failures else 'PASSED'} — "
              f"{failures} of {len(groups)} self-test group(s) failed")
        return max(engine_rc, guard_rc)

    guard_rc: int | None = None
    if not _ui_guard_in_scope(argv):
        print(f"SKIP {_UI_GUARD_ID} (no changed path reads the Web shards)")
    else:
        guard_rc = run_ui_guard(mutate=False, web_dir=web_dir)

    engine_checks = summary.get("checks", 0)
    engine_failures = summary.get("failures", 0)
    # Two sources meet here — the engine's results and this script's own guard — so the
    # engine's count has to agree with the exit code it came with before either is printed.
    # A summary whose failure count disagrees with the run it summarises is the defect this
    # script prints its own single summary line to prevent; refuse it loudly instead.
    if (engine_failures > 0) != (engine_rc == 1):
        print(f"check-invariants: the engine reported {engine_failures} failure(s) but exited "
              f"{engine_rc}; refusing to print a summary it cannot back", file=sys.stderr)
        return 2

    checks = engine_checks
    failures = engine_failures
    if guard_rc is not None:
        checks += 1
        failures += 1 if guard_rc else 0
    if not checks:                                             # the engine already explained
        return engine_rc

    declared = summary.get("declared", 0)
    selected = summary.get("selected", declared)
    guard_note = f" + {_UI_GUARD_ID}" if guard_rc is not None else ""
    scope = "" if selected == declared else f" (scoped: {selected} of {declared} declared{guard_note})"
    print(f"check-invariants: {checks} check(s), {failures} failure(s){scope}")
    if engine_rc == 2 or guard_rc == 2:
        return 2
    return 1 if failures else 0                               # the same count the line printed


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
