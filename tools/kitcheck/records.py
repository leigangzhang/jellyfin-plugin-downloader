"""The note-class guard: a record's folder, its `Class:` line, and the closed set must agree."""

from __future__ import annotations

from .core import die

import hashlib
import json
import re

from .core import iter_files

RECORD_PATH = re.compile(r"^\.agents/notes/(?P<lifecycle>[^/]+)/(?P<klass>[^/]+)/(?P<file>[^/]+)$")


def module_table(root: Path, check: dict) -> list[str]:
    """Read the declared modules from the first column of the table under the check's heading.

    The list lives in the records home rather than in the configuration, so the reader who
    manages it and the reader who is about to name a record find it in one place. The gate
    reads that one table; a second rendering of it here would be a second fact.
    """
    heading = check.get("areasHeading")
    if not isinstance(heading, str) or not heading:
        die(f"check {check['id']!r}: 'areasHeading' is required when 'areasIn' is declared")
    doc = root / check["areasIn"]
    if not doc.exists():
        die(f"check {check['id']!r}: {check['areasIn']} does not exist, so the module list has no home")
    marker = f"\n{heading}\n"
    start = doc.read_text(encoding="utf-8").find(marker)
    if start < 0:
        die(f"check {check['id']!r}: {check['areasIn']} has no {heading!r} section, so the module list has no home")
    section = doc.read_text(encoding="utf-8")[start + len(marker):]
    end = section.find("\n## ")
    if end >= 0:
        section = section[:end]
    names: list[str] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        head = cells[0] if cells else ""
        if not head or head in ("Module", "模块") or set(head) <= set("-: "):
            continue
        names.append(head)
    if not names:
        die(f"check {check['id']!r}: the {heading!r} table in {check['areasIn']} declares no module")
    return names


def check_note_class(root: Path, check: dict) -> list[str]:
    """Reject a record whose path class, `Class:` line, and topic module do not agree with the sets.

    A lifecycle's `TEMPLATE.md` is a matched subject only: the skeleton keeps the corpus
    non-empty before the first record exists, and a skeleton is not a record to classify.

    When the check declares `areasIn`, a topic title must open with a subject the table
    there declares. Every record, whatever its date: the list is a list of subjects, and a
    date that exempts records from it would only make the list something to negotiate with.
    """
    classes = check.get("classes")
    if not isinstance(classes, list) or not classes:
        die(f"check {check['id']!r}: 'classes' must be a non-empty list")
    lifecycles = check.get("lifecycles", ["proposed", "implemented", "rejected"])
    if not isinstance(lifecycles, list) or not lifecycles:
        die(f"check {check['id']!r}: 'lifecycles' must be a non-empty list when present")
    areas = module_table(root, check) if check.get("areasIn") is not None else None
    violations: list[str] = []
    for relative, text in iter_files(root, check["patterns"]):
        if relative.rsplit("/", 1)[-1].startswith("TEMPLATE"):
            continue
        match = RECORD_PATH.match(relative)
        if match is None:
            violations.append(f"{relative}: a record lives at .agents/notes/<lifecycle>/<class>/<file>")
            continue
        lifecycle, klass, base = match.group("lifecycle"), match.group("klass"), match.group("file")
        if areas is not None:
            tokens = base.split("-")
            module = tokens[3] if len(tokens) > 3 else ""
            if module not in areas:
                violations.append(
                    f"{relative}: the topic title opens with {module!r}, which is not a subject the table declares;"
                    f" reuse one of {', '.join(areas)} or add a row to {check['areasIn']}")
        if lifecycle not in lifecycles:
            violations.append(f"{relative}: unknown lifecycle {lifecycle!r}; expected one of {', '.join(lifecycles)}")
        if klass not in classes:
            violations.append(f"{relative}: unknown class {klass!r}; the closed set is {', '.join(classes)}")
            continue
        declared = next((line for line in text.splitlines() if line.startswith("Class:")), None)
        if declared is None:
            violations.append(f"{relative}: no 'Class:' line, so the path class {klass!r} is unconfirmed")
        elif declared.split(":", 1)[1].strip() != klass:
            violations.append(
                f"{relative}: Class line {declared.split(':', 1)[1].strip()!r} disagrees with the path class {klass!r}"
            )
    return violations


MODULE_README = "\n".join([
    "# Records",
    "",
    "## Modules",
    "",
    "| Module | What it covers |",
    "|---|---|",
    "| `notes` | The records themselves. |",
    "| `docs` | The standing documents. |",
    "",
])


SELF_TEST_CASES = (
    (
        "note-class",
        {
            "id": "self-note-class",
            "kind": "note-class",
            "patterns": [".agents/notes/*/*/*.md"],
            "classes": ["feature", "bug-fix", "simplification", "architecture", "process", "testing"],
        },
        {".agents/notes/implemented/architecture/decision.md": "Status: implemented\nClass: process\n\n## Problem\nx\n"},
        {".agents/notes/implemented/architecture/decision.md": "Status: implemented\nClass: architecture\n\n## Problem\nx\n"},
    ),
    # A topic title must open with a subject the table declares, and an old date is no way
    # out: the first pair proves the rule, the second proves the list cannot be waited past.
    (
        "note-class",
        {
            "id": "self-note-area",
            "kind": "note-class",
            "patterns": [".agents/notes/*/*/*.md"],
            "classes": ["feature", "bug-fix", "simplification", "architecture", "process", "testing"],
            "areasIn": ".agents/notes/README.md",
            "areasHeading": "## Modules",
        },
        {
            ".agents/notes/README.md": MODULE_README,
            ".agents/notes/implemented/process/2026-10-08-widget-thing.md": "Class: process\n\n## Problem\nx\n",
        },
        {
            ".agents/notes/README.md": MODULE_README,
            ".agents/notes/implemented/process/2026-10-08-notes-widget-thing.md": "Class: process\n\n## Problem\nx\n",
        },
    ),
    (
        "note-class",
        {
            "id": "self-note-area-old",
            "kind": "note-class",
            "patterns": [".agents/notes/*/*/*.md"],
            "classes": ["feature", "bug-fix", "simplification", "architecture", "process", "testing"],
            "areasIn": ".agents/notes/README.md",
            "areasHeading": "## Modules",
        },
        {
            ".agents/notes/README.md": MODULE_README,
            ".agents/notes/implemented/process/2020-01-01-widget-thing.md": "Class: process\n\n## Problem\nx\n",
        },
        {
            ".agents/notes/README.md": MODULE_README,
            ".agents/notes/implemented/process/2020-01-01-notes-widget-thing.md": "Class: process\n\n## Problem\nx\n",
        },
    ),
)
