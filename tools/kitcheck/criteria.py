"""The criteria guard: every acceptance criterion cites a declared check or surface, and survives the move."""

from __future__ import annotations

from .policies import section_variants

from .core import die, section_body

import json
import re

from .core import iter_files

DEFAULT_CRITERION_ID = r"\[([A-Za-z][A-Za-z0-9]*-?\d+)\]"


OWNER_TOKEN = re.compile(r"`([a-z][a-z0-9-]*)`")


def check_criteria_traced(root: Path, check: dict) -> list[str]:
    """Reject a criterion that has no id, no owner, or an owner this repository never declared.

    Owners are read from `declaredIn`, so a criterion can only cite a check or surface
    this repository actually configures. Only bare lowercase names in backticks are
    resolved; commands and paths stay free text because no check can run them.
    """
    sections = check.get("sections")
    if not isinstance(sections, list) or not sections:
        die(f"check {check['id']!r}: 'sections' must be a non-empty list")
    declarations = [section_variants(section, check) for section in sections]
    declared_rel = check.get("declaredIn")
    if not isinstance(declared_rel, str) or declared_rel == "":
        die(f"check {check['id']!r}: 'declaredIn' must be a non-empty string")
    id_source = check.get("idPattern", DEFAULT_CRITERION_ID)
    if not isinstance(id_source, str) or id_source == "":
        die(f"check {check['id']!r}: 'idPattern' must be a non-empty string")
    try:
        id_regex = re.compile(id_source)
    except re.error as exc:
        die(f"check {check['id']!r}: invalid idPattern {id_source!r}: {exc}")
    if id_regex.groups != 1:
        die(f"check {check['id']!r}: 'idPattern' must contain exactly one capture group")

    declared_path = root / declared_rel
    if not declared_path.is_file():
        die(f"check {check['id']!r}: declaredIn not found: {declared_rel}")
    try:
        declared = json.loads(declared_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"check {check['id']!r}: cannot read {declared_rel}: {exc}")
    if not isinstance(declared, dict):
        die(f"check {check['id']!r}: {declared_rel} must be a JSON object")
    owners: set[str] = set()
    for field, key in (("checks", "id"), ("surfaces", "name")):
        entries = declared.get(field, [])
        if not isinstance(entries, list):
            die(f"check {check['id']!r}: {declared_rel} field {field!r} must be a list")
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get(key), str) and entry[key]:
                owners.add(entry[key])
    if not owners:
        die(f"check {check['id']!r}: {declared_rel} declares no check id or surface name to trace to")

    violations: list[str] = []
    for relative, text in iter_files(root, check["patterns"]):
        headings = {line for line in text.splitlines() if line.startswith("## ")}
        present = [
            matched
            for variants in declarations
            for matched in [next((variant for variant in variants if variant in headings), None)]
            if matched is not None
        ]
        if not present:
            violations.append(f"{relative}: contains none of {sections!r}, so no criterion is traceable")
            continue
        for section in present:
            bullets = [
                line.strip()
                for line in section_body(text, section)
                if line.strip().startswith(("- ", "* ")) or re.match(r"^\d+\. ", line.strip())
            ]
            if not bullets:
                violations.append(f"{relative}: {section!r} carries no criterion bullets to trace")
                continue
            for bullet in bullets:
                ids = id_regex.findall(bullet)
                if not ids:
                    violations.append(f"{relative}: criterion in {section!r} has no id: {bullet[:70]!r}")
                    continue
                if not any(name in owners for name in OWNER_TOKEN.findall(bullet)):
                    violations.append(
                        f"{relative}: criterion {ids[0]} names no declared check or surface"
                        f" — cite one of {sorted(owners)} in backticks",
                    )
    return violations


SELF_CRITERIA_CHECK = {
    "id": "self-criteria",
    "kind": "criteria-traced",
    "patterns": [".agents/notes/implemented/*.md"],
    "sections": ["## Acceptance criteria", "## Testing"],
    "declaredIn": "tools/workflow.json",
}


SELF_CRITERIA_FILES = {
    "tools/workflow.json": json.dumps({"checks": [{"id": "budget"}], "surfaces": [{"name": "source"}]}),
    ".agents/notes/implemented/TEMPLATE.md": (
        "# Decision\n\n## Testing\n\n- [A1] `budget` rejects a file over its line budget.\n"
    ),
}


SELF_TEST_CASES = (
    (
        "criteria-traced",
        SELF_CRITERIA_CHECK,
        {
            **SELF_CRITERIA_FILES,
            ".agents/notes/implemented/TEMPLATE.md": "# Decision\n\n## Testing\n\n- [A1] the feature works as intended.\n",
        },
        dict(SELF_CRITERIA_FILES),
    ),
)
