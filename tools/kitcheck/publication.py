"""The publication guard: every document classified exactly once, and `public` names real files."""

from __future__ import annotations

from fnmatch import fnmatch
import json

from .core import iter_files, read_manifest

def check_publish_manifest(root: Path, check: dict) -> list[str]:
    """Reject an unclassified file, an ambiguous file, and a stale public pattern.

    Publication is a projection, and the manifest is its only switch. A file matching
    the check's `patterns` and no declared pattern would never appear anywhere, and
    nothing else would notice; that silence is the defect this kind exists to break. In
    the other direction a declared pattern that matches no file is a stale claim — but
    only on the `public` side, where the claim promises that the file exists. An
    `internal` pattern may be an empty shelf, and every entry there must say why.
    """
    manifest = read_manifest(root, check, "public")
    manifest_rel = check["manifest"]

    internal = manifest.get("internal", [])
    if not isinstance(internal, list):
        die(f"check {check['id']!r}: manifest {manifest_rel} field 'internal' must be a list")

    declared: list[tuple[str, str]] = []
    violations: list[str] = []
    for index, pattern in enumerate(manifest["public"]):
        if not isinstance(pattern, str) or pattern == "":
            violations.append(f"{manifest_rel} public[{index}]: must be a non-empty string")
            continue
        if any(magic in pattern for magic in "*?["):
            violations.append(
                f"{manifest_rel} public[{index}] {pattern!r}: publication is a per-file promise"
                " — name the file, do not publish a directory",
            )
        declared.append((pattern, "public"))
    for index, entry in enumerate(internal):
        label = f"{manifest_rel} internal[{index}]"
        if not isinstance(entry, dict):
            violations.append(f"{label}: not a JSON object")
            continue
        pattern = entry.get("pattern")
        reason = entry.get("reason")
        if not isinstance(pattern, str) or pattern == "":
            violations.append(f"{label}: missing non-empty 'pattern'")
            continue
        if not isinstance(reason, str) or reason.strip() == "":
            violations.append(f"{label} {pattern!r}: missing 'reason' — say why this file stays in the repository")
        declared.append((pattern, "internal"))

    seen: set[str] = set()
    for pattern, _ in declared:
        if pattern in seen:
            violations.append(f"{manifest_rel}: pattern {pattern!r} is declared twice — one file has one class")
        seen.add(pattern)

    corpus = [relative for relative, _ in iter_files(root, check["patterns"])]
    for pattern, side in declared:
        if side == "public" and not any(fnmatch(relative, pattern) for relative in corpus):
            violations.append(
                f"{manifest_rel}: public pattern {pattern!r} matches no file"
                " — a published promise with nothing behind it is a broken link",
            )

    for relative in corpus:
        hits = [(pattern, side) for pattern, side in declared if fnmatch(relative, pattern)]
        if not hits:
            violations.append(
                f"{relative}: matches no declared pattern — classify it public or internal in {manifest_rel}",
            )
        elif len(hits) > 1:
            detail = ", ".join(f"{pattern!r} ({side})" for pattern, side in hits)
            violations.append(f"{relative}: matches {len(hits)} declared patterns ({detail}) — a file has exactly one class")
    return violations


SELF_TEST_CASES = (
    (
        "publish-manifest",
        {
            "id": "self-publish",
            "kind": "publish-manifest",
            "patterns": ["docs/*.md"],
            "manifest": "docs/publish.json",
        },
        {
            "docs/publish.json": json.dumps({"public": ["docs/a.md"], "internal": []}),
            "docs/a.md": "# A\n",
            "docs/b.md": "# B\n",
        },
        {
            "docs/publish.json": json.dumps({
                "public": ["docs/a.md"],
                "internal": [{"pattern": "docs/b.md", "reason": "an internal draft, not a page"}],
            }),
            "docs/a.md": "# A\n",
            "docs/b.md": "# B\n",
        },
    ),
)
