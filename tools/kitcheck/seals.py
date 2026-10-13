"""The seal guard: a sealed record is frozen, and the seal and the record must name the same date."""

from __future__ import annotations

from .core import die

from fnmatch import fnmatch
import hashlib
import json
import re

from .core import iter_files, read_manifest

SEAL_DIGEST = re.compile(r"^[0-9a-f]{64}$")


SEAL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sealed_digest(text: str) -> str:
    """Return the SHA-256 of a file's text, with newlines normalized by the reader.

    `read_text` folds CRLF to LF, so the same content seals to the same value in a
    checkout with different line endings. The seal freezes content; the line-ending
    convention of the machine that wrote it is not content.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_sealed_manifest(root: Path, check: dict) -> list[str]:
    """Reject an unsealed archive file, a drifted seal, and a stale registration.

    An archive is append-only, so its `sealed` list starts empty and this check does not
    require it to be populated: the guard is live because every file matching `patterns`
    other than the manifest itself must be registered, so the first record dropped into
    the archive fails until it is sealed. The manifest is excluded from its own corpus
    because a registry cannot seal the file that holds the seals.

    Each entry binds three facts: the path (which must fall inside the scanned patterns),
    the digest of the file's text, and the `Archived: <date>` line the file must carry.
    A seal whose file changed, whose header date disagrees, or whose path left the
    scanned range is drift; so is a sealed file that no longer exists.
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
    sealed = manifest.get("sealed")
    if not isinstance(sealed, list):
        die(
            f"check {check['id']!r}: manifest {manifest_rel} needs a 'sealed' list"
            " (it may be empty: an archive starts with nothing sealed)",
        )

    patterns: list[str] = check["patterns"]
    violations: list[str] = []
    seen: dict[str, int] = {}
    registered: set[str] = set()
    for index, entry in enumerate(sealed):
        label = f"{manifest_rel} sealed[{index}]"
        if not isinstance(entry, dict):
            violations.append(f"{label}: not a JSON object")
            continue
        fields: dict[str, str] = {}
        for field in ("path", "sha256", "archived", "reason"):
            value = entry.get(field)
            if not isinstance(value, str) or value.strip() == "":
                violations.append(f"{label}: missing non-empty {field!r}")
            else:
                fields[field] = value
        if len(fields) != 4:
            continue
        path, digest, archived, _ = fields["path"], fields["sha256"], fields["archived"], fields["reason"]

        if path in seen:
            violations.append(f"{label}: duplicate seal for {path!r} (first at sealed[{seen[path]}])")
            continue
        seen[path] = index
        registered.add(path)

        if not SEAL_DIGEST.match(digest):
            violations.append(f"{label} {path}: 'sha256' must be 64 lowercase hex characters")
        if not SEAL_DATE.match(archived):
            violations.append(f"{label} {path}: 'archived' must be yyyy-mm-dd")
        if not any(fnmatch(path, pattern) for pattern in patterns):
            violations.append(f"{label}: {path} matches none of {patterns!r}, so its seal is never verified")
            continue

        target = root / path
        if not target.is_file():
            violations.append(f"{label}: sealed file not found: {path}")
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            violations.append(f"{label}: cannot read {path}: {exc}")
            continue

        observed = sealed_digest(text)
        if observed != digest:
            violations.append(
                f"{label}: {path} changed after it was sealed — recorded {digest[:12]}…, found {observed[:12]}…"
                "; an archived record is frozen: restore its bytes, or retire it and write a new record",
            )
        if not any(line.strip() == f"Archived: {archived}" for line in text.splitlines()):
            violations.append(
                f"{label}: {path} carries no 'Archived: {archived}' line"
                " — the seal and the record it seals must name the same date",
            )

    for relative, _ in iter_files(root, patterns):
        if relative == manifest_rel:
            continue
        if relative not in registered:
            violations.append(
                f"{relative}: is in the archive but carries no seal — register it in {manifest_rel};"
                " an archive is append-only, so nothing lands here unsealed",
            )
    return violations


SELF_SEALED_TEXT = "# Old record\n\nArchived: 2026-01-01\n\nThis record stopped guiding anyone.\n"


SELF_SEALED_CHECK = {
    "id": "self-sealed",
    "kind": "sealed-manifest",
    "patterns": ["archive/*"],
    "manifest": "archive/manifest.json",
}


SELF_SEALED_FILES = {
    "archive/manifest.json": json.dumps({"sealed": [
        {
            "path": "archive/a.md",
            "sha256": hashlib.sha256(SELF_SEALED_TEXT.encode("utf-8")).hexdigest(),
            "archived": "2026-01-01",
            "reason": "superseded by .agents/notes/implemented/2026-01-02-successor.md",
        },
    ]}),
    "archive/a.md": SELF_SEALED_TEXT,
}


SELF_TEST_CASES = (
    (
        "sealed-manifest",
        SELF_SEALED_CHECK,
        {
            **SELF_SEALED_FILES,
            "archive/manifest.json": json.dumps({"sealed": [
                {
                    "path": "archive/a.md",
                    "sha256": "0" * 64,
                    "archived": "2026-01-01",
                    "reason": "superseded",
                },
            ]}),
        },
        dict(SELF_SEALED_FILES),
    ),
)
