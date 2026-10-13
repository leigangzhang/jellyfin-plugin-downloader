"""The capability guard: the registry and the markers in source must agree."""

from __future__ import annotations

from .core import die

import json

from .core import iter_files


# The capability kinds the registry accepts. `seam` is the only kind that claims
# the three roles; everything else must justify its kind in the entry's `note`.
CAPABILITY_KINDS = ("seam", "core", "service", "bundle")


def check_capability_registry(root: Path, check: dict) -> list[str]:
    """Reject an incomplete classification or a registry that drifted from source.

    Roles are declared by hand in `manifest`; existence is discovered from
    `capability: <key>` markers under `discover.patterns`. A `seam` must name a
    definition, at least one provider, and at least one consumer, and every named
    path must exist; any other kind must carry the reason it is not a seam in
    `note`. Registered keys and discovered markers must match in both directions.
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
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        die(f"check {check['id']!r}: manifest {manifest_rel} needs a non-empty 'capabilities' list")

    violations: list[str] = []
    seen: dict[str, int] = {}
    declared: set[str] = set()
    for index, entry in enumerate(capabilities):
        label = f"{manifest_rel} capabilities[{index}]"
        if not isinstance(entry, dict):
            violations.append(f"{label}: not a JSON object")
            continue
        key = entry.get("key")
        kind = entry.get("kind")
        definition = entry.get("definition")
        note = entry.get("note")
        if not isinstance(key, str) or key == "":
            violations.append(f"{label}: missing non-empty 'key'")
            continue
        if key in seen:
            violations.append(f"{label}: duplicate key {key!r} (first at capabilities[{seen[key]}])")
            continue
        seen[key] = index
        declared.add(key)

        if kind not in CAPABILITY_KINDS:
            violations.append(f"{label} {key}: 'kind' must be one of {CAPABILITY_KINDS}")
        if not isinstance(definition, str) or definition == "":
            violations.append(f"{label} {key}: missing non-empty 'definition'")
        elif not (root / definition).exists():
            violations.append(f"{label} {key}: definition does not exist: {definition}")
        if not isinstance(note, str) or note.strip() == "":
            violations.append(f"{label} {key}: missing 'note' — say why these roles split or do not")

        providers = entry.get("providers", [])
        consumers = entry.get("consumers", [])
        for field, values in (("providers", providers), ("consumers", consumers)):
            if not isinstance(values, list) or any(not isinstance(value, str) or value == "" for value in values):
                violations.append(f"{label} {key}: {field!r} must be a list of non-empty strings")
                continue
            for value in values:
                if not (root / value).exists():
                    violations.append(f"{label} {key}: {field[:-1]} does not exist: {value}")
        if kind == "seam":
            if not isinstance(providers, list) or not providers:
                violations.append(f"{label} {key}: a seam needs at least one provider")
            if not isinstance(consumers, list) or not consumers:
                violations.append(f"{label} {key}: a seam needs at least one consumer")

    discover = check.get("discover")
    if discover is not None:
        if not isinstance(discover, dict):
            die(f"check {check['id']!r}: 'discover' must be a JSON object")
        discover_patterns = discover.get("patterns")
        marker = discover.get("marker")
        if not isinstance(discover_patterns, list) or not discover_patterns or any(
            not isinstance(pattern, str) or pattern == "" for pattern in discover_patterns
        ):
            die(f"check {check['id']!r}: 'discover.patterns' must be a non-empty list of strings")
        if not isinstance(marker, str) or marker == "":
            die(f"check {check['id']!r}: 'discover.marker' must be a non-empty string")

        discovered: set[str] = set()
        for relative, text in iter_files(root, discover_patterns):
            for number, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped.startswith(marker):
                    continue
                tail = stripped[len(marker):].strip()
                if tail == "":
                    violations.append(f"{relative}:{number}: {marker!r} marker carries no key")
                    continue
                discovered.add(tail.split()[0])
        for key in sorted(discovered - declared):
            violations.append(
                f"{key!r} is declared by a marker but missing from {manifest_rel} — classify it",
            )
        for key in sorted(declared - discovered):
            violations.append(
                f"capability {key!r} is registered in {manifest_rel} but has no {marker!r} marker — stale entry",
            )
    return violations


SELF_CAPABILITY_MANIFEST = {
    "capabilities": [
        {
            "key": "engine",
            "kind": "seam",
            "definition": "contracts/kinds.md",
            "providers": ["src/runner.py"],
            "consumers": ["src/consumer.py"],
            "note": "One runner executes the declared kinds.",
        },
    ],
}
SELF_CAPABILITY_CHECK = {
    "id": "self-capability",
    "kind": "capability-registry",
    "patterns": ["capabilities/*.md"],
    "manifest": "capabilities/registry.json",
    "discover": {"patterns": ["src/*"], "marker": "# capability:"},
}
SELF_CAPABILITY_FILES = {
    "capabilities/registry.json": json.dumps(SELF_CAPABILITY_MANIFEST),
    "capabilities/TEMPLATE.md": "# Capability template\n",
    "contracts/kinds.md": "# Kinds\n",
    "src/runner.py": "# capability: engine\n",
    "src/consumer.py": "uses the engine\n",
}


SELF_TEST_CASES = (
    (
        "capability-registry",
        SELF_CAPABILITY_CHECK,
        {
            **SELF_CAPABILITY_FILES,
            "capabilities/registry.json": json.dumps({"capabilities": [
                {**SELF_CAPABILITY_MANIFEST["capabilities"][0], "providers": []},
            ]}),
        },
        dict(SELF_CAPABILITY_FILES),
    ),
)
