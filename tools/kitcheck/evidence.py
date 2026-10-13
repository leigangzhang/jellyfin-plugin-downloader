"""The evidence-record guard: a record must state what it concluded, and a machine verdict must add up."""

from __future__ import annotations

import re
from pathlib import Path

from .core import iter_files

GENERATED = re.compile(
    r"^Verdict: (?P<verdict>PASS|FAIL) — (?P<passed>\d+) passed, (?P<failed>\d+) failed, "
    r"(?P<unknown>\d+) could not run \(of (?P<total>\d+) runnable command\(s\)\)$",
    re.MULTILINE,
)
MANUAL = re.compile(r"^Verdict: MANUAL — \S.*$", re.MULTILINE)
BODY = re.compile(r"^## \S", re.MULTILINE)
ENTRY = re.compile(r"^- \*\*(?P<state>PASS|FAIL|UNKNOWN|UNFILLED|manual)\b[^*]*\*\* — (?P<body>.*)$", re.MULTILINE)
COMMAND = re.compile(r"`([^`]+)`")
HEADER = ("# Evidence record", "Recorded:", "Scope:", "## Changed paths and their surfaces")


def check_evidence_record(root: Path, check: dict) -> list[str]:
    """Reject a record that states no verdict, or states one its own entries do not support.

    Every record in this home says what it concluded, in one of the two forms this kit can
    write. `tools/run-evidence.py` writes the first: a verdict, its three counts, and an
    entry for every declared command, placeholder, and manual item. The three counts are
    over the runnable commands alone — a placeholder was never run, and a manual entry is
    never counted as verified here. A person writes the second form when the tool cannot
    see the tree: `Verdict: MANUAL — <why>`. A machine verdict is a claim about its own
    entries, so it is cross-checked; a record whose counts disagree with the entries below
    them was edited, truncated, or written by a regressed generator, and a verdict nobody
    can trust is worse than no record.
    """
    violations: list[str] = []
    for relative, text in iter_files(root, check["patterns"]):
        generated = GENERATED.search(text)
        if generated is None:
            if MANUAL.search(text) is None:
                violations.append(
                    f"{relative}: no `Verdict:` line; a record that does not state what it concluded is not evidence")
            elif BODY.search(text) is None:
                violations.append(f"{relative}: a manual verdict over an empty body has nothing to be manual about")
            continue
        for marker in HEADER:
            if marker not in text:
                violations.append(f"{relative}: no {marker!r}; a generated record a reader cannot place is not evidence")
        commands: dict[str, str] = {}
        for entry in ENTRY.finditer(text):
            state, body = entry.group("state"), entry.group("body")
            if state == "manual":
                continue
            named = COMMAND.search(body)
            if named is None:
                violations.append(f"{relative}: an entry reads {state!r} without naming the command it is about")
                continue
            commands[named.group(1)] = state
        passed = sum(1 for state in commands.values() if state == "PASS")
        failed = sum(1 for state in commands.values() if state == "FAIL")
        unknown = sum(1 for state in commands.values() if state == "UNKNOWN")
        stated = (int(generated.group("passed")), int(generated.group("failed")), int(generated.group("unknown")))
        if stated != (passed, failed, unknown):
            violations.append(
                f"{relative}: the verdict says {stated[0]} passed / {stated[1]} failed / {stated[2]} could not run,"
                f" while its entries say {passed} / {failed} / {unknown}")
            continue
        if int(generated.group("total")) != passed + failed + unknown:
            violations.append(
                f"{relative}: the verdict counts {generated.group('total')} runnable command(s),"
                f" while its entries account for {passed + failed + unknown}")
        expected = "PASS" if not failed and not unknown else "FAIL"
        if generated.group("verdict") != expected:
            violations.append(
                f"{relative}: the verdict is {generated.group('verdict')!r} while its entries imply {expected!r}")
    return violations


def _generated(verdict: str, counts: tuple[int, int, int, int], entries: list[str]) -> str:
    """A record in the shape run-evidence writes, carrying the counts and entries a probe needs."""
    return "\n".join([
        "# Evidence record",
        "",
        "Recorded: 2026-01-01T00:00:00Z",
        "Scope: worktree only",
        f"Verdict: {verdict} — {counts[0]} passed, {counts[1]} failed, {counts[2]} could not run"
        f" (of {counts[3]} runnable command(s))",
        "",
        "## Changed paths and their surfaces",
        "",
        "### prose",
        "",
        "why",
        "",
        "- `a.md`",
        "",
        *entries,
    ]) + "\n"


A_GENERATED_CHECK = {"id": "self-evidence", "kind": "evidence-record", "patterns": ["dev/evidence/*.md"]}

SELF_TEST_CASES = (
    # A machine verdict that contradicts its own entries, and the same record with them agreeing.
    (
        "evidence-record",
        A_GENERATED_CHECK,
        {"dev/evidence/a.md": _generated("PASS", (1, 0, 0, 1), ["- **FAIL (exit 1)** — `cmd`"])},
        {"dev/evidence/a.md": _generated("PASS", (1, 0, 0, 1), ["- **PASS (exit 0)** — `cmd`"])},
    ),
    # A manual verdict over a body, and one over nothing at all.
    (
        "evidence-record",
        A_GENERATED_CHECK,
        {"dev/evidence/b.md": "# What was run\n\nVerdict: MANUAL — run by hand\n"},
        {"dev/evidence/b.md": "# What was run\n\nVerdict: MANUAL — run by hand\n\n## What this covers\n\nprose\n"},
    ),
    # A placeholder is never run, so it must not be counted as a command that could not run.
    (
        "evidence-record",
        A_GENERATED_CHECK,
        {"dev/evidence/c.md": _generated("FAIL", (1, 0, 1, 1), [
            "- **PASS (exit 0)** — `cmd`",
            "- **UNFILLED** — `<a placeholder>` (a placeholder is not evidence)",
        ])},
        {"dev/evidence/c.md": _generated("PASS", (1, 0, 0, 1), [
            "- **PASS (exit 0)** — `cmd`",
            "- **UNFILLED** — `<a placeholder>` (a placeholder is not evidence)",
        ])},
    ),
)
