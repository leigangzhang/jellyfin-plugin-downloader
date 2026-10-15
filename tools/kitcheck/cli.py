"""The command line: load the configuration, run every declared check, report, and exit.

The summary line and the exit code read the same count. `report()` folds the results
`run_checks()` returned through one `summarise()` call, so a check that fails is in both
or in neither — whatever kind it is, and whether its runner decides from file text or
while the check runs (`note-class` reads the records home's module table mid-run). A
summary that reports fewer failures than the run has is worse than no summary at all: it
is the single line a reader skims, and it reads as a green light over a red run.

`--self-test` carries the negative controls for that counter, beside the counter itself:
`summary-counts-every-kind` proves a failing check of any kind is counted, and
`RED-OK summary-ignores-a-kind` proves the assertion rejects a counter that skips one —
the shape this fix removed.

Exit codes:
  0  every check passed, or --self-test passed
  1  at least one violation, or a self-test failed
  2  usage or environment error (bad config, unsupported check kind, invalid regex)
"""

from __future__ import annotations

from pathlib import Path
import argparse
import sys

from .core import DEFAULT_CONFIG, changed_paths, die, load_config, select_checks
from .registry import run_checks, self_test


def summarise(results: list[tuple[str, list[str]]]) -> tuple[int, int]:
    """Return (checks run, failures) for the results a run is about to report.

    One fold over the whole result list, which is also what the exit code is derived from:
    the summary cannot count some kinds and not others, because it does not look at kinds.
    """
    return len(results), sum(1 for _check_id, violations in results if violations)


def report(results: list[tuple[str, list[str]]], summary: dict[str, int] | None = None,
           out=print, count=summarise) -> int:
    """Print one line per check plus the summary, and return the exit code.

    The line and the exit code read the same results, in the same run, with no second
    bookkeeping to drift: the summary folds them through `count` (the counter, by default),
    and the exit code asks them the raw question. With `summary` given, the caller owns the
    final line — it may have more parts to report than this engine ran, like the UI
    behaviour guard tools/check-invariants.py adds — and only the counts are written back
    into it. `count` is a parameter so the self-test can run this real path with a leaking
    counter and prove the run still exits red.
    """
    for check_id, violations in results:
        if violations:
            out(f"FAIL {check_id}")
            for violation in violations:
                out(f"  {violation}")
        else:
            out(f"PASS {check_id}")
    checks, failures = count(results)
    if summary is not None:
        summary["checks"] = checks
        summary["failures"] = failures
    else:
        out(f"check-invariants: {checks} check(s), {failures} failure(s)")
    # A regression in the counter can make the line wrong; it must not also make a red run
    # exit green, so the exit code is the raw question the results answer. The `summary-*`
    # self-test probes below are what catch the wrong line — `tools/check-invariants.py`
    # additionally refuses to print a summary whose count disagrees with this exit code.
    return 1 if any(violations for _check_id, violations in results) else 0


def self_test_summary() -> int:
    """Negative control for `summarise`: a counter that misses a kind must not pass.

    The removed defect was exactly this shape: a run whose last line reported `失败 0`
    while the process exited 1, because the count never saw the check that failed. The
    probe applies the same assertion to the real counter and to a counter of that shape,
    so reintroducing the shape turns `--self-test` red.
    """
    failed: list[str] = []
    results = [("note-class", ["a record's title opens with a subject the table does not declare"]),
               ("budget", [])]

    def agrees(counts: tuple[int, int]) -> bool:
        return counts == summarise(results)

    if agrees((2, 1)):
        print("PASS summary-counts-every-kind: a failing note-class is counted (1 of 2 checks)")
    else:
        failed.append("summary-counts-every-kind")
        print(f"FAIL summary-counts-every-kind: counted {summarise(results)}, expected (2, 1)")

    def legacy_summarise(rows: list[tuple[str, list[str]]]) -> tuple[int, int]:
        """The removed shape: a counter that only looks at some of the failing kinds."""
        return len(rows), sum(1 for check_id, violations in rows
                              if violations and check_id.startswith("budget"))

    if not agrees(legacy_summarise(results)) and legacy_summarise(results)[1] == 0:
        print("RED-OK summary-ignores-a-kind: a counter that skips note-class reports "
              f"{legacy_summarise(results)[1]} failure(s) against 1 real failure, and the "
              "assertion rejects it")
    else:
        failed.append("summary-ignores-a-kind")
        print("FAIL summary-ignores-a-kind: a counter that misses a failing kind was accepted")

    # The strongest form of the guard: run this module's real reporting path with that same
    # leaking counter, and require the run to fail anyway. A wrong summary line is
    # recoverable; a red run that exits green is not.
    leaking_rc = report(results, summary={}, out=lambda _line: None, count=legacy_summarise)
    if legacy_summarise(results)[1] == 0 and leaking_rc == 1:
        print("RED-OK summary-leak-keeps-the-exit-code-red: a leaking counter reports 0 "
              "failure(s) and the run still exits 1")
    else:
        failed.append("summary-leak-keeps-the-exit-code-red")
        print("FAIL summary-leak-keeps-the-exit-code-red: a leaking counter turned the run green")

    if failed:
        print(f"check-invariants: self-test FAILED for {', '.join(failed)}", file=sys.stderr)
        return 1
    print("check-invariants: the summary counts every failing check, the exit code reads the "
          "same results, and a mis-counting summary is rejected")
    return 0


def main(argv: list[str] | None = None, summary: dict[str, int] | None = None) -> int:
    """Entry point.

    `summary` is filled with `declared`, `selected`, `checks`, and `failures`, and this
    function then leaves the final line to the caller: a wrapper that runs more checks than
    this engine knows about must print one summary over the whole run, not two lines whose
    numbers disagree. Without `summary`, the engine prints its own summary line.
    """
    parser = argparse.ArgumentParser(
        prog="check-invariants",
        description="Run the executable conventions declared in the workflow config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("root", nargs="?", default=".", help="tree to scan (default: the current directory)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="check declarations (default: tools/workflow.json)")
    parser.add_argument("--self-test", action="store_true", help="prove each check kind can fail, using throwaway fixtures")
    parser.add_argument("--list", action="store_true", help="list the declared checks and exit")
    parser.add_argument("--changed", metavar="BASE", help="run only the checks that read the paths changed against BASE (iteration mode; the full set is the boundary run)")
    parser.add_argument("--worktree-only", action="store_true", help="with --changed: ignore committed changes and read the worktree")
    args = parser.parse_args(argv)

    if args.self_test:
        return max(self_test(), self_test_summary())

    config = load_config(args.config)

    root = Path(args.root).resolve()
    if not root.is_dir():
        die(f"not a directory: {root}")

    checks = config["checks"]
    declared = len(config["checks"])
    if summary is not None:
        summary.update({"declared": declared, "selected": declared, "checks": 0, "failures": 0})
    if args.changed:
        paths, merge_base = changed_paths(args.changed, worktree_only=args.worktree_only, cwd=root, prog="check-invariants")
        checks = select_checks(config, paths)
        scope = "the worktree" if args.worktree_only else f"{args.changed} (merge base {merge_base[:12]})"
        print(f"check-invariants: {len(paths)} changed path(s) against {scope}; "
              f"{len(checks)} of {declared} declared check(s) read them")
        if summary is not None:
            summary["selected"] = len(checks)
        if not checks:
            print("check-invariants: no declared check reads these paths; the full set is the delivery-boundary run")
            return 0

    if args.list:
        for check in checks:
            print(f"  {check['id']}  ({check['kind']})  {check.get('why', '')}")
        return 0

    return report(run_checks(root, checks), summary=summary)
