"""The mirror guard: a pasted block bound to its source region."""

from __future__ import annotations

from .core import die

from fnmatch import fnmatch

import json

from .core import iter_files, section_body

def fenced_blocks(text: str, info: str) -> list[tuple[list[str], bool]]:
    """Return (body lines, closed) for every fence whose info string equals `info`."""
    blocks: list[tuple[list[str], bool]] = []
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if inside:
            if stripped.startswith("```"):
                blocks.append((body, True))
                inside = False
            else:
                body.append(line)
            continue
        if stripped.startswith("```") and stripped[3:].strip() == info:
            inside = True
            body = []
    if inside:
        blocks.append((body, False))
    return blocks


def normalize_block(lines: list[str]) -> list[str]:
    """Strip trailing whitespace per line and drop leading/trailing blank lines."""
    normalized = [line.rstrip() for line in lines]
    while normalized and not normalized[0].strip():
        normalized.pop(0)
    while normalized and not normalized[-1].strip():
        normalized.pop()
    return normalized


def first_difference(left: list[str], right: list[str]) -> str:
    """Describe the first line where two normalized blocks differ."""
    for index in range(max(len(left), len(right))):
        lhs = left[index] if index < len(left) else "<missing>"
        rhs = right[index] if index < len(right) else "<missing>"
        if lhs != rhs:
            return f"first difference at line {index + 1}: doc {lhs!r} != source {rhs!r}"
    return "blocks are equal"


def check_source_mirror(root: Path, check: dict) -> list[str]:
    """Reject any registered doc block that differs from its source region.

    A mirror is one-to-one in both directions: an entry with no matching block is
    reported, a block whose fence info string is registered anywhere but which has no
    entry in its own doc is reported, and a doc outside this check's `patterns` is
    rejected so an unreachable registration cannot pass silently.
    """
    manifest_rel = check.get("manifest")
    if not isinstance(manifest_rel, str) or manifest_rel == "":
        die(f"check {check['id']!r}: 'manifest' must be a non-empty string")
    manifest_path = root / manifest_rel
    if not manifest_path.is_file():
        die(f"check {check['id']!r}: manifest not found: {manifest_rel}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"check {check['id']!r}: cannot read manifest {manifest_rel}: {exc}")
    if not isinstance(manifest, dict):
        die(f"check {check['id']!r}: manifest {manifest_rel} must be a JSON object")
    mirrors = manifest.get("mirrors")
    if not isinstance(mirrors, list) or not mirrors:
        die(f"check {check['id']!r}: manifest {manifest_rel} needs a non-empty 'mirrors' list")

    patterns: list[str] = check["patterns"]
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    registered: dict[tuple[str, str], int] = {}
    for index, mirror in enumerate(mirrors):
        label = f"{manifest_rel} mirrors[{index}]"
        if not isinstance(mirror, dict):
            violations.append(f"{label}: not a JSON object")
            continue
        fields: dict[str, str] = {}
        for field in ("doc", "fence", "source", "begin", "end"):
            value = mirror.get(field)
            if not isinstance(value, str) or value == "":
                violations.append(f"{label}: missing non-empty {field!r}")
            else:
                fields[field] = value
        if len(fields) != 5:
            continue
        doc, fence, source = fields["doc"], fields["fence"], fields["source"]
        begin, end = fields["begin"], fields["end"]

        if (doc, fence) in seen:
            violations.append(f"{label}: duplicate mirror for {doc} fence {fence!r}")
            continue
        seen.add((doc, fence))
        registered[(doc, fence)] = registered.get((doc, fence), 0) + 1

        if not any(fnmatch(doc, pattern) for pattern in patterns):
            violations.append(f"{label}: doc {doc} matches none of {patterns!r}, so it is never scanned")
            continue

        doc_path = root / doc
        source_path = root / source
        if not doc_path.is_file():
            violations.append(f"{label}: doc not found: {doc}")
            continue
        if not source_path.is_file():
            violations.append(f"{label}: source not found: {source}")
            continue

        try:
            doc_text = doc_path.read_text(encoding="utf-8")
            source_lines = source_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            violations.append(f"{label}: cannot read {doc} or {source}: {exc}")
            continue

        blocks = fenced_blocks(doc_text, fence)
        unterminated = [body for body, closed in blocks if not closed]
        closed = [body for body, closed in blocks if closed]
        if unterminated:
            violations.append(f"{label}: unterminated fence with info {fence!r} in {doc}")
        if not closed:
            violations.append(f"{label}: no fenced block with info {fence!r} in {doc}")
            continue
        if len(closed) > 1:
            violations.append(f"{label}: {len(closed)} blocks with info {fence!r} in {doc}; a mirror must be unique")
            continue

        begin_hits = [i for i, line in enumerate(source_lines) if line.strip() == begin]
        end_hits = [i for i, line in enumerate(source_lines) if line.strip() == end]
        if len(begin_hits) != 1:
            violations.append(f"{label}: begin marker {begin!r} matches {len(begin_hits)} line(s) in {source}")
            continue
        if len(end_hits) != 1:
            violations.append(f"{label}: end marker {end!r} matches {len(end_hits)} line(s) in {source}")
            continue
        if end_hits[0] < begin_hits[0]:
            violations.append(f"{label}: end marker precedes begin marker in {source}")
            continue

        region = source_lines[begin_hits[0] + 1:end_hits[0]]
        doc_block = normalize_block(closed[0])
        source_block = normalize_block(region)
        if doc_block != source_block:
            violations.append(
                f"{label}: {doc} does not match {source} between markers"
                f" — {first_difference(doc_block, source_block)}",
            )

    # Reverse direction: a registered fence claimed in a doc but never entered is
    # drift too. Only fence info strings named by the manifest count as mirrors, so
    # unrelated fenced examples in the same docs stay free.
    fence_infos = {fence for _, fence in registered}
    for relative, text in iter_files(root, patterns):
        for fence in sorted(fence_infos):
            if registered.get((relative, fence), 0) > 0:
                continue
            blocks = [body for body, closed in fenced_blocks(text, fence) if closed]
            if blocks:
                violations.append(
                    f"{relative}: {len(blocks)} block(s) with info {fence!r} have no mirror entry"
                    f" in {manifest_rel} — register the block or remove it",
                )
    return violations


SELF_MIRROR_MANIFEST = {
    "mirrors": [
        {
            "doc": "contracts/a.md",
            "fence": "text mirror",
            "source": "src/a.py",
            "begin": "# begin: a",
            "end": "# end: a",
        },
    ],
}


SELF_TEST_CASES = (
    (
        "source-mirror",
        {
            "id": "self-mirror",
            "kind": "source-mirror",
            "patterns": ["contracts/*.md"],
            "manifest": "contracts/mirrors.json",
        },
        {
            "contracts/mirrors.json": json.dumps(SELF_MIRROR_MANIFEST),
            "contracts/a.md": "# A\n\n```text mirror\nkeep = 1\n```\n",
            "src/a.py": "# begin: a\nkeep = 2\n# end: a\n",
        },
        {
            "contracts/mirrors.json": json.dumps(SELF_MIRROR_MANIFEST),
            "contracts/a.md": "# A\n\n```text mirror\nkeep = 2\n```\n",
            "src/a.py": "# begin: a\nkeep = 2\n# end: a\n",
        },
    ),
)
