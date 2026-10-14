"""The rendering model: anchors, structure signatures, and consistency records.

A record is a projection of a pair, not a second copy of it: this module computes the
section hashes, renders them in the canonical text, and parses that text back. The
structure signature lives here too, because "the same document in another language" is
a claim about a rendered shape.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .model import (
    ATX,
    FENCE,
    HASH_LINE,
    HEADING_LINE,
    HTML_ID,
    KEY_LINE,
    ZH_SUFFIX,
    die,
    heading_text,
    mask_code,
    normalize_links,
    sections,
    slug,
)


def github_slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, keep letters/numbers/_/space/hyphen, spaces to hyphens."""
    return re.sub(r"[^\w \-]", "", heading.lower(), flags=re.UNICODE).replace(" ", "-")


def rendered_heading(raw: str) -> str:
    """Heading text as GitHub renders it: inline markup gone, its content kept."""
    line = re.sub(r"^ {0,3}#{1,6}[ \t]*", "", raw.strip())
    line = re.sub(r"`([^`]*)`", r"\1", line)
    line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"<[^>]+>", "", line)
    return line.replace("**", "").replace("*", "")


def document_anchors(text: str) -> set[str]:
    """Every fragment a document exposes: heading slugs plus explicit `<a id>` anchors."""
    masked = mask_code(text)
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for raw in HEADING_LINE.findall(masked):
        base = github_slug(rendered_heading(raw))
        if base in seen:
            seen[base] += 1
            base = f"{base}-{seen[base]}"
        else:
            seen[base] = 0
        anchors.add(base)
    anchors.update(HTML_ID.findall(masked))
    return anchors


def structure_signature(text: str) -> tuple:
    """The shape a counterpart must mirror: headings, fence info strings, tables, lists.

    Text is not compared — a translation is text. What is compared is the document's
    skeleton: a table that lost a row, a list that lost an item, a heading one level
    deeper, or a fence whose info string drifted is a different document wearing the
    same name. The host renderer compares the same four things for the pairs it governs;
    this makes the rule hold for every pair in this kit.
    """
    masked = mask_code(text)
    headings = tuple(len(m.group(1)) for m in re.finditer(r"^(#{1,6}) ", masked, re.M))
    fences = tuple(match.group(2).strip() for match in FENCE.finditer(text))
    tables: list[tuple[int, int]] = []
    lists: list[tuple[str, int]] = []
    inside_fence = False
    rows = 0
    kind: str | None = None
    items = 0
    def flush() -> None:
        nonlocal rows, kind, items
        if rows:
            tables.append((rows, table_cols))
        if kind is not None:
            lists.append((kind, items))
        rows, kind, items = 0, None, 0
    table_cols = 0
    for line in masked.split("\n"):
        if FENCE.match(line):
            flush(); inside_fence = not inside_fence; continue
        if inside_fence:
            continue
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cols = len([c for c in stripped.strip("|").split("|")])
            if cols != table_cols:
                flush(); table_cols = cols
            rows += 1
            continue
        marker = re.match(r"^(\s*)([-*+]|\d+[.)])\s+\S", line)
        if marker and (not line.startswith(" ") or kind is not None):
            this = "ordered" if marker.group(2)[0].isdigit() else "bullet"
            if kind != this:
                flush(); kind = this
            items += 1
            continue
        flush()
    flush()
    return headings, fences, tuple(tables), tuple(lists)


def compute_record(
    prefix: str,
    anchor: str,
    root: Path,
    anchors: set[str],
    targets: list[str],
) -> dict[str, dict[str, str]]:
    """Recompute the section hashes of one pair from its current contents."""
    en = (root / anchor).read_text(encoding="utf-8")
    zh = (root / (anchor[: -len(".md")] + ZH_SUFFIX)).read_text(encoding="utf-8")
    normalized_en = normalize_links(prefix, anchor, en, targets, root, anchors)
    normalized_zh = normalize_links(prefix, anchor[: -len(".md")] + ZH_SUFFIX, zh, targets, root, anchors)
    groups_en = sections(normalized_en)
    groups_zh = sections(normalized_zh)
    if len(groups_en) != len(groups_zh):
        die(f"{anchor}: {len(groups_en) - 1} heading(s) against {len(groups_zh) - 1} on the Chinese side")
    keys: set[str] = set()
    ancestors: list[tuple[int, str]] = []
    record: dict[str, dict[str, str]] = {}
    for group_en, group_zh in zip(groups_en, groups_zh):
        heading = group_en["heading"]
        path = "/"
        if heading is not None:
            depth = len(ATX.match(heading).group(1))
            while ancestors and ancestors[-1][0] >= depth:
                ancestors.pop()
            ancestors.append((depth, slug(heading_text(heading))))
            path = "/" + "/".join(part for _depth, part in ancestors)
        key = path
        repeat = 2
        while key in keys:
            key = f"{path}~{repeat}"
            repeat += 1
        keys.add(key)
        if not group_en["blocks"] and not group_zh["blocks"]:
            continue
        record[key] = {
            "en": hashlib.sha256("\n".join(group_en["blocks"]).encode()).hexdigest()[:16],
            "zh": hashlib.sha256("\n".join(group_zh["blocks"]).encode()).hexdigest()[:16],
        }
    return record


def render_record(anchor: str, record: dict[str, dict[str, str]], host_canonical: bool, prefix: str) -> str:
    """Canonical record text: one location-independent form for every pair.

    A record travels with the kit, so it never names the enclosing repository's path and
    prints the kit's own command. `host_canonical` and `prefix` stay in the signature for
    callers that already hold them; the canonical text no longer depends on either.
    """
    name = anchor.rsplit("/", 1)[-1]
    header = [
        f"# Bilingual-pair consistency record for {name} (docs/i18n.md): per heading section, a hash",
        "# of its English and Chinese blocks outside fenced code blocks.",
        "# After editing either side, bring the other along and re-record with:",
        f"#   python3 tools/pair-docs.py --write {anchor}",
    ]
    entries = [line for key, hashes in record.items() for line in (f"{key}:", f"  en: {hashes['en']}", f"  zh: {hashes['zh']}")]
    return "\n".join([*header, *entries, ""])


def parse_record(text: str) -> dict[str, dict[str, str]] | None:
    """Parse a record, returning None when malformed or duplicated."""
    lines = [line for line in text.split("\n") if line != "" and not line.startswith("#")]
    record: dict[str, dict[str, str]] = {}
    for index in range(0, len(lines), 3):
        key = KEY_LINE.match(lines[index] if index < len(lines) else "")
        en = HASH_LINE.match(lines[index + 1] if index + 1 < len(lines) else "")
        zh = HASH_LINE.match(lines[index + 2] if index + 2 < len(lines) else "")
        if key is None or key.group(1) in record or en is None or zh is None:
            return None
        if en.group(1) != "en" or zh.group(1) != "zh":
            return None
        record[key.group(1)] = {"en": en.group(2), "zh": zh.group(2)}
    return record


def switcher_line(text: str) -> str | None:
    """The line immediately after the H1, or None when no H1 exists."""
    lines = text.split("\n")
    start = next((index for index, line in enumerate(lines) if line.startswith("# ")), None)
    if start is None:
        return None
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            return None
        if line.strip() == "":
            continue
        return line.strip()
    return None
