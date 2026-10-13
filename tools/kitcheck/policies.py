"""Document-shaped guards: budgets, required sections, forbidden patterns."""

from __future__ import annotations

from .core import die

import re

from .core import count_lines, count_words, iter_files, section_body

def check_budget(root: Path, check: dict) -> list[str]:
    """Reject any matching file that exceeds a declared line or word budget."""
    limits: dict[str, int] = {}
    for field in ("maxLines", "maxWords"):
        value = check.get(field)
        if value is None:
            continue
        if not isinstance(value, int) or value < 1:
            die(f"check {check['id']!r}: {field!r} must be a positive integer")
        limits[field] = value
    if not limits:
        die(f"check {check['id']!r}: needs 'maxLines', 'maxWords', or both")

    counters = {"maxLines": count_lines, "maxWords": count_words}
    violations: list[str] = []
    for relative, text in iter_files(root, check["patterns"]):
        for field, limit in limits.items():
            counted = counters[field](text)
            if counted > limit:
                violations.append(f"{relative}: {counted} {field[3:].lower()}, budget is {limit}")
    return violations


def section_variants(section: object, check: dict) -> list[str]:
    """Accepted heading spellings for one required section, in declaration order.

    A bilingual document may translate its headings, so one requirement accepts
    every declared spelling of the same section; the entry is either the single
    heading or a list of the spellings that satisfy it.
    """
    if isinstance(section, str):
        variants: list[object] = [section]
    elif isinstance(section, list) and section:
        variants = list(section)
    else:
        die(f"check {check['id']!r}: each 'sections' entry must be a heading string or a non-empty list of them")
    for variant in variants:
        if not isinstance(variant, str) or not variant.startswith("## "):
            die(f"check {check['id']!r}: section spelling {variant!r} must be a level-two heading")
    return [str(variant) for variant in variants]


def check_required_sections(root: Path, check: dict) -> list[str]:
    """Reject any matching file that is missing a section or left its body empty."""
    sections = check.get("sections")
    minimum = check.get("minBodyLines", 1)
    if not isinstance(sections, list) or not sections:
        die(f"check {check['id']!r}: 'sections' must be a non-empty list")
    if not isinstance(minimum, int) or minimum < 1:
        die(f"check {check['id']!r}: 'minBodyLines' must be a positive integer")
    violations: list[str] = []
    for relative, text in iter_files(root, check["patterns"]):
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        for section in sections:
            variants = section_variants(section, check)
            matched = next((variant for variant in variants if variant in headings), None)
            if matched is None:
                wanted = " or ".join(repr(variant) for variant in variants)
                violations.append(f"{relative}: missing section {wanted}")
                continue
            filled = [line for line in section_body(text, matched) if line.strip()]
            if len(filled) < minimum:
                violations.append(f"{relative}: section {matched!r} has an empty body (needs {minimum} non-blank line(s))")
    return violations


def check_forbidden_regex(root: Path, check: dict) -> list[str]:
    """Reject any matching file that contains a forbidden pattern."""
    patterns = check.get("regex")
    if not isinstance(patterns, list) or not patterns:
        die(f"check {check['id']!r}: 'regex' must be a non-empty list")
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in patterns:
        try:
            compiled.append((pattern, re.compile(pattern)))
        except re.error as exc:
            die(f"check {check['id']!r}: invalid regex {pattern!r}: {exc}")
    violations: list[str] = []
    for relative, text in iter_files(root, check["patterns"]):
        for number, line in enumerate(text.splitlines(), start=1):
            for pattern, regex in compiled:
                if regex.search(line):
                    violations.append(f"{relative}:{number}: matches forbidden pattern {pattern!r}")
    return violations


SELF_TEST_CASES = (
    (
        "budget",
        {"id": "self-budget", "kind": "budget", "patterns": ["AGENTS.md"], "maxLines": 2},
        {"AGENTS.md": "# Rules\nalpha\nbeta\ngamma\n"},
        {"AGENTS.md": "# Rules\nalpha\n"},
    ),
    (
        "required-sections",
        {
            "id": "self-sections",
            "kind": "required-sections",
            "patterns": [".agents/notes/*.md"],
            "sections": ["## Problem", "## Alternatives considered"],
            "minBodyLines": 1,
        },
        {".agents/notes/decision.md": "## Problem\nsomething broke\n\n## Alternatives considered\n\n## Decision\nfixed\n"},
        {".agents/notes/decision.md": "## Problem\nsomething broke\n\n## Alternatives considered\n\n**Do nothing.** It stays broken.\n\n## Decision\nfixed\n"},
    ),
    (
        "forbidden-regex",
        {"id": "self-regex", "kind": "forbidden-regex", "patterns": ["*"], "regex": ["BEGIN-FAKE-SECRET"]},
        {"leak.txt": "token = 'BEGIN-FAKE-SECRET'\n"},
        {"clean.txt": "token = 'redacted'\n"},
    ),
)
