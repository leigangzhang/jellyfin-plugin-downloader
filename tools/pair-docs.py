#!/usr/bin/env python3
"""Write or verify this kit's bilingual-pair consistency records.

A pair is `X.md` (English), `X.zh.md` (Chinese), and `X.i18n.yaml` (the
consistency record). `--check` recomputes every record from the two documents
and reports a missing sibling, an absent language switcher, a fenced code block
that differs between the sides, or a section whose recorded hash no longer
matches its content; `--write` rewrites the records. Scope, exclusions, and the
anchors whose records must stay byte-identical to a host repository's renderer
come from `pairing` in `tools/workflow.json`, this kit's single configuration
file. The hash model is the one `docs/i18n/README.md` describes: per heading
section, a 16-hex SHA-256 of that side's top-level blocks, with fenced code
blocks left out.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import posixpath
import re
import sys
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote


def _load_engine():
    """Load tools/kitcheck by path, so the tier switch resolves identically for every tool.

    A plain `import kitcheck` resolves only while `tools/` happens to sit on `sys.path`:
    the script's own directory when this file is run, or a loader that already registered
    the package. The suite loads this file by path from the repository root, so the package
    is loaded here rather than assumed.
    """
    if "kitcheck" not in sys.modules:
        package = Path(__file__).resolve().parent / "kitcheck"
        spec = importlib.util.spec_from_file_location(
            "kitcheck", package / "__init__.py", submodule_search_locations=[str(package)])
        if spec is None or spec.loader is None:
            raise SystemExit("tools/kitcheck is missing")
        module = importlib.util.module_from_spec(spec)
        sys.modules["kitcheck"] = module
        spec.loader.exec_module(module)
    return sys.modules["kitcheck"]


_ENGINE = _load_engine()

HEADING_LINE = re.compile(r"^#{1,6} .+$", re.MULTILINE)
HTML_ID = re.compile(r'<a\\s+id="([^"]+)"')
ZH_SUFFIX = ".zh.md"
RECORD_SUFFIX = ".i18n.yaml"
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$", re.M)
LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
FRONTMATTER = re.compile(r"^---\n[\s\S]*?\n---(?:\n|$)")
INLINE_LINK = re.compile(r"\]\(\s*(?:<(?P<angle>[^>]*)>|(?P<plain>[^\s()<>]+))")
DEFINITION_LINK = re.compile(r"^ {0,3}\[(?P<label>[^\]]+)\]:[ \t]*(?P<dest>\S+)", re.MULTILINE)
SWITCHER_EN = re.compile(r"^English \| \[中文\]\((?P<target>[^)\s]+)\)$")
SWITCHER_ZH = re.compile(r"^\[English\]\((?P<target>[^)\s]+)\) \| 中文$")
KEY_LINE = re.compile(r"^(/[^\s:#]*):$")
HASH_LINE = re.compile(r"^ {2}(en|zh): ([0-9a-f]{16})$")


def die(message: str) -> None:
    """Stop with a configuration error rather than a half-run check."""
    raise SystemExit(f"pair-docs: {message}")


def load_pairing(root: Path) -> dict:
    """Read the `pairing` declaration from the kit's configuration file."""
    config_path = root / "tools" / "workflow.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read {config_path}: {exc}")
    pairing = config.get("pairing")
    if not isinstance(pairing, dict):
        die("tools/workflow.json needs a 'pairing' object")
    for field in ("patterns", "exclude", "hostCanonical"):
        value = pairing.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            die(f"pairing.{field} must be a list of strings")
    if not pairing["patterns"]:
        die("pairing.patterns must not be empty")
    return pairing



def anchor_paths(root: Path, pairing: dict) -> list[str]:
    """Every `*.md` file in scope as an English-side anchor, in sorted order."""
    listed = _ENGINE.corpus(root)
    found = []
    for path in root.rglob("*.md"):
        relative = path.relative_to(root).as_posix()
        if listed is not None and relative not in listed:
            continue
        if relative.endswith(ZH_SUFFIX):
            continue
        if not _ENGINE.matches(relative, pairing["patterns"]):
            continue
        if _ENGINE.matches(relative, pairing["exclude"]):
            continue
        found.append(relative)
    return sorted(found)


def discover_prefix(root: Path) -> str:
    """Repository-relative path of the kit root, for host-compatible link hashes."""
    for parent in (root, *root.parents):
        if (parent / ".git").exists():
            return "" if parent == root else root.relative_to(parent).as_posix()
    return ""


def repo_path(prefix: str, relative: str) -> str:
    """Join the kit-relative path onto the enclosing repository path."""
    return f"{prefix}/{relative}" if prefix else relative


def slug(text: str) -> str:
    """Heading slug, matching the host record's section keys."""
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value or "section"


def heading_text(raw: str) -> str:
    """Heading text with inline markup removed, for the section-key slug."""
    line = re.sub(r"^ {0,3}#{1,6}[ \t]*", "", raw.strip())
    line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"`([^`]*)`", r"\1", line)
    line = re.sub(r"\*\*|__|\*|_", "", line)
    return line.strip()


def indent_of(line: str) -> int:
    """Count leading spaces, for list-continuation detection."""
    return len(line) - len(line.lstrip(" "))


def fence_end(lines: list[str], index: int) -> int:
    """Index of the closing fence line, or the last line when it never closes."""
    marker = FENCE.match(lines[index]).group(1)
    cursor = index + 1
    while cursor < len(lines):
        closer = FENCE.match(lines[cursor])
        if closer and closer.group(1)[0] == marker[0] and len(closer.group(1)) >= len(marker):
            return cursor
        cursor += 1
    return len(lines) - 1


def block_end(lines: list[str], index: int) -> int:
    """Last line of the top-level block that starts at `index`."""
    if FENCE.match(lines[index]):
        return fence_end(lines, index)
    if LIST_ITEM.match(lines[index]):
        cursor = index + 1
        while cursor < len(lines):
            line = lines[cursor]
            if line.strip() == "" or LIST_ITEM.match(line) or indent_of(line) >= 2:
                cursor += 1
                continue
            break
        end = cursor - 1
        while end > index and lines[end].strip() == "":
            end -= 1
        return end
    cursor = index
    while cursor + 1 < len(lines) and lines[cursor + 1].strip() != "":
        following = lines[cursor + 1]
        if ATX.match(following) or FENCE.match(following) or LIST_ITEM.match(following):
            break
        cursor += 1
    return cursor


def sections(text: str) -> list[dict]:
    """Preamble plus one section per heading; fenced code blocks are dropped."""
    lines = text.split("\n")
    blocks: list[str] = []
    start = 0
    frontmatter = FRONTMATTER.match(text)
    if frontmatter:
        blocks.append(text[: frontmatter.end()])
        start = frontmatter.end()
    result = [{"heading": None, "blocks": blocks}]
    index = 0
    position = 0
    while index < len(lines) and position < start:
        position += len(lines[index]) + 1
        index += 1
    while index < len(lines):
        line = lines[index]
        if line.strip() == "":
            index += 1
            continue
        end = block_end(lines, index)
        raw = "\n".join(lines[index : end + 1])
        if ATX.match(line):
            result.append({"heading": line, "blocks": [raw]})
        elif not FENCE.match(line):
            if not LIST_ITEM.match(line):
                # A paragraph block starts at its first non-space character, so a
                # one-to-three-space indent is layout, not content.
                first, _, rest = raw.partition("\n")
                raw = first.lstrip(" ") + (("\n" + rest) if rest else "")
            result[-1]["blocks"].append(raw)
        index = end + 1
    return result


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """Every fenced code block as its (info string, body), in document order."""
    lines = text.split("\n")
    found: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        opener = FENCE.match(lines[index])
        if not opener:
            index += 1
            continue
        marker = opener.group(1)
        body: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            closer = FENCE.match(lines[cursor])
            if closer and closer.group(1)[0] == marker[0] and len(closer.group(1)) >= len(marker):
                break
            body.append(lines[cursor])
            cursor += 1
        found.append((opener.group(2).strip(), "\n".join(body)))
        index = cursor + 1
    return found


def mask_code(text: str) -> str:
    """Blank fenced blocks and inline code spans, preserving every offset."""
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        opener = FENCE.match(lines[index])
        if not opener:
            index += 1
            continue
        marker = opener.group(1)
        end = fence_end(lines, index)
        for target in range(index, end + 1):
            lines[target] = " " * len(lines[target])
        index = end + 1
    stripped = "\n".join(lines)
    return re.sub(r"`[^`\n]*`", lambda match: " " * len(match.group(0)), stripped)


def document_links(text: str) -> list[tuple[int, str, int, int]]:
    """Every link destination, as (line number, url, start offset, end offset)."""
    masked = mask_code(text)
    found: list[tuple[int, str, int, int]] = []
    for pattern, groups in ((INLINE_LINK, ("angle", "plain")), (DEFINITION_LINK, ("dest",))):
        for match in pattern.finditer(masked):
            chosen = next(((name, match.group(name)) for name in groups if match.group(name) is not None), None)
            if chosen is None:
                continue
            name, url = chosen
            line = masked.count("\n", 0, match.start(name)) + 1
            found.append((line, url, match.start(name), match.end(name)))
    return found


def resolve_pair(prefix: str, anchor: str, url: str, anchors: set[str], root: Path) -> tuple[str, bool] | None:
    """Resolve a relative link to a paired document: its source path and authored locale."""
    path = url.split("#", 1)[0].split("?", 1)[0]
    if path == "" or path.startswith(("//", "/")) or ":" in path.split("/")[0]:
        return None
    base = posixpath.dirname(repo_path(prefix, anchor))
    joined = posixpath.normpath(posixpath.join(base, path))
    stripped = joined[len(prefix) + 1 :] if prefix and joined.startswith(f"{prefix}/") else joined
    if not (root / stripped).is_file():
        return None
    if joined.endswith(ZH_SUFFIX):
        source = joined[: -len(ZH_SUFFIX)] + ".md"
    elif joined.endswith(".md"):
        source = joined
    else:
        return None
    if source not in {repo_path(prefix, item) for item in anchors}:
        return None
    return source, joined.endswith(ZH_SUFFIX)


def normalize_links(prefix: str, anchor: str, text: str, targets: list[str], root: Path, anchors: set[str]) -> str:
    """Rewrite links into a governed document at a location-independent placeholder.

    Only a record that must stay byte-identical across every repository carrying the kit
    is normalized, and only for the documents the kit's own corpus covers; every other
    record hashes the text as authored. The placeholder names the kit-relative target, so
    cloning the kit or copying it into a project root leaves every hash unchanged.
    """
    if not targets:
        return text
    accepted = {repo_path(prefix, item) for item in targets}
    replacements: list[tuple[int, int, str]] = []
    for _line, url, start, end in document_links(text):
        resolved = resolve_pair(prefix, anchor, url, anchors, root)
        if resolved is None or resolved[0] not in accepted:
            continue
        suffix = url[len(url.split("#", 1)[0].split("?", 1)[0]) :]
        source = resolved[0]
        if prefix and source.startswith(f"{prefix}/"):
            source = source[len(prefix) + 1 :]
        replacements.append((start, end, f"dsh-translation-target:{source}{suffix}"))
    for start, end, value in sorted(replacements, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def link_locale_violations(
    prefix: str,
    path: str,
    anchor: str,
    text: str,
    anchors: set[str],
    pairing: dict,
    root: Path,
) -> list[str]:
    """Reject a relative link to a paired document that uses the wrong side's locale.

    A link into a paired document uses the side's own locale, except inside the files the
    host repository's gate governs: that renderer keeps the authored path for every target
    outside its own corpus, so a governed pair must agree byte for byte there. A link to a
    governed README still follows the locale, because that target is in its corpus.
    """
    switcher = switcher_line(text)
    skip = None
    lines = text.split("\n")
    if switcher is not None:
        skip = next((index + 1 for index, line in enumerate(lines) if line.strip() == switcher), None)
    zh_side = path.endswith(ZH_SUFFIX)
    host_targets = {repo_path(prefix, item) for item in pairing["hostCanonical"]}
    host_governed = anchor in pairing["hostCanonical"]
    out = []
    for line, url, _start, _end in document_links(text):
        if skip is not None and line == skip:
            continue
        resolved = resolve_pair(prefix, path, url, anchors, root)
        if resolved is None:
            continue
        source, authored_zh = resolved
        wanted_zh = False if host_governed and source not in host_targets else zh_side
        if authored_zh == wanted_zh:
            continue
        wanted = "Chinese" if wanted_zh else "English"
        actual = "Chinese" if authored_zh else "English"
        out.append(f"{path}:{line}: link {url!r} addresses the {actual} side; this file needs the {wanted} side")
    return out



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


def structure_violations(anchor: str, en_text: str, zh_text: str) -> list[str]:
    """Reject a pair whose two sides are not the same shape."""
    labels = ("heading depths", "fence info strings", "table rows x columns", "list kinds and item counts")
    en, zh = structure_signature(en_text), structure_signature(zh_text)
    out = []
    for label, a, b in zip(labels, en, zh):
        if a != b:
            out.append(f"{anchor}: {label} diverge between the pair: {a!r} vs {b!r}")
    return out


@lru_cache(maxsize=None)
def absent_home_prefixes(root: Path) -> tuple[str, ...]:
    """The stage homes the switch declares absent, as literal path prefixes.

    The lifecycle map keeps all eleven stations while the tree carries only the installed
    ones, so a document may link to a station this project does not run. A link into an
    absent home states what that station is; it is not a promise about a file here.
    """
    prefixes = []
    for home in _ENGINE.load_tier_switch(root).get("absent_homes", []):
        prefix = _ENGINE.literal_prefix(home)
        if prefix:
            prefixes.append(prefix)
    return tuple(prefixes)


def link_violations(root: Path, path: str, text: str) -> list[str]:
    """Reject a relative link whose target is missing, and a fragment that names no anchor.

    A relative link is a claim that a file exists at that path. Nothing else in this kit
    reads links — the host renderer compares the two sides of a pair, not the filesystem —
    so a link that drifted one directory level too far is invisible until a reader clicks
    it. That is why the check lives here rather than in a reviewer's habit. The language
    switcher line is skipped only when it really is one, so a file that carries no
    switcher still has every line checked.
    """
    switcher = switcher_line(text)
    skip = None
    if switcher is not None and (SWITCHER_EN.match(switcher) or SWITCHER_ZH.match(switcher)):
        skip = next((index + 1 for index, line in enumerate(text.split("\n")) if line.strip() == switcher), None)
    out = []
    absent = absent_home_prefixes(root)
    for line, url, _start, _end in document_links(text):
        if skip is not None and line == skip:
            continue
        if url.startswith(("http://", "https://", "mailto:")):
            continue
        path_part, separator, fragment = url.partition("#")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path) or ".", path_part)) if path_part else path
        if any(target.startswith(prefix) for prefix in absent):
            continue
        target_file = root / target
        if not target_file.exists():
            out.append(f"{path}:{line}: link {url!r} names no file")
            continue
        if not separator or not fragment or target_file.is_dir():
            continue
        fragment = unquote(fragment)
        if fragment not in document_anchors(target_file.read_text(encoding="utf-8")):
            out.append(f"{path}:{line}: fragment #{fragment} names no heading or anchor in {target}")
    return out


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



def switcher_target_violations(anchor: str, en_text: str, zh_text: str, stem: str) -> list[str]:
    """The switcher must lead to the counterpart, not to the page it sits on.

    `English | [中文](X.zh.md)` on the English side and `[English](X.md) | 中文` on
    the Chinese side are the convention ([i18n.md](../docs/i18n.md#links-and-the-language-switcher)).
    A switcher that names its own side is present but useless, and nothing else catches it:
    the host renderer governs only the files it can see.
    """
    out = []
    en_switcher = switcher_line(en_text)
    zh_switcher = switcher_line(zh_text)
    if en_switcher is None or zh_switcher is None:
        return out                       # an absent switcher is reported by the caller
    en_targets = re.findall(r"\]\(([^)\s]+)\)", en_switcher)
    zh_targets = re.findall(r"\]\(([^)\s]+)\)", zh_switcher)
    if f"{stem}.zh.md" not in en_targets:
        out.append(f"{anchor}.md: the English switcher must link {stem}.zh.md, but links {en_targets or 'nothing'}")
    if f"{stem}.md" not in zh_targets:
        out.append(f"{anchor}.zh.md: the Chinese switcher must link {stem}.md, but links {zh_targets or 'nothing'}")
    return out


def record_diff(recorded: dict, current: dict) -> list[str]:
    """One message per section whose recorded hash no longer matches."""
    out = []
    for key, hashes in current.items():
        if key not in recorded:
            out.append(f"section {key} has unconfirmed content")
        elif recorded[key] != hashes:
            sides = [side for side in ("en", "zh") if recorded[key][side] != hashes[side]]
            out.append(f"section {key} changed since confirmation ({', '.join(sides)})")
    for key in recorded:
        if key not in current:
            out.append(f"section {key} is recorded but carries no translated content")
    return out


def check_anchor(root: Path, prefix: str, anchor: str, anchors: set[str], pairing: dict) -> list[str]:
    """Every violation that stops one pair from being complete and in step."""
    host_canonical = anchor in pairing["hostCanonical"]
    targets = pairing["hostCanonical"] if host_canonical else []
    violations: list[str] = []
    zh_rel = anchor[: -len(".md")] + ZH_SUFFIX
    record_rel = anchor[: -len(".md")] + RECORD_SUFFIX
    for sibling in (zh_rel, record_rel):
        if not (root / sibling).is_file():
            violations.append(f"{anchor}: incomplete pair — missing {sibling}")
    if violations:
        return violations
    en_text = (root / anchor).read_text(encoding="utf-8")
    zh_text = (root / zh_rel).read_text(encoding="utf-8")
    for path, text, pattern in ((anchor, en_text, SWITCHER_EN), (zh_rel, zh_text, SWITCHER_ZH)):
        line = switcher_line(text)
        match = pattern.match(line) if line is not None else None
        if match is None:
            violations.append(f"{path}: missing language switcher `{pattern.pattern}` after the H1")
            continue
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), match.group("target")))
        if not (root / target).is_file():
            violations.append(f"{path}: switcher target {match.group('target')!r} does not exist")
    if fenced_blocks(en_text) != fenced_blocks(zh_text):
        violations.append(f"{anchor} ↔ {zh_rel}: fenced code blocks differ (info string or body)")
    violations.extend(link_locale_violations(prefix, anchor, anchor, en_text, anchors, pairing, root))
    violations.extend(link_locale_violations(prefix, zh_rel, anchor, zh_text, anchors, pairing, root))
    violations.extend(link_violations(root, anchor, en_text))
    violations.extend(link_violations(root, zh_rel, zh_text))
    violations.extend(switcher_target_violations(anchor, en_text, zh_text, Path(anchor).name[:-3]))
    violations.extend(structure_violations(anchor, en_text, zh_text))
    current = compute_record(prefix, anchor, root, anchors, targets)
    recorded = parse_record((root / record_rel).read_text(encoding="utf-8"))
    if recorded is None:
        violations.append(f"{record_rel}: malformed consistency record")
        return violations
    violations.extend(f"{record_rel}: out of sync — {message}" for message in record_diff(recorded, current))
    canonical = render_record(anchor, current, host_canonical, prefix)
    if canonical != (root / record_rel).read_text(encoding="utf-8"):
        violations.append(f"{record_rel}: not in canonical form (re-record with --write)")
    return violations


def select_anchors(anchors: list[str], corpus: list[str]) -> list[str]:
    """Resolve the command-line anchors against the corpus a run recorded.

    `./name` is the only spelling that changes; a path that names a hidden directory
    (`.agents/…`) keeps its leading dot, because that dot is part of the name.
    """
    if not anchors:
        return corpus
    scope = set(corpus)
    selected: list[str] = []
    for item in anchors:
        anchor = item[2:] if item.startswith("./") else item
        if anchor not in scope:
            die(f"{anchor} is not an in-scope pair (see pairing in tools/workflow.json)")
        selected.append(anchor)
    return selected


def main() -> int:
    """Run the requested mode over the whole corpus or the named anchors."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("anchors", nargs="*", help="English-side paths; default is every in-scope document")
    parser.add_argument("--check", action="store_true", help="recompute the records and report drift")
    parser.add_argument("--write", action="store_true", help="rewrite the records from current contents")
    parser.add_argument("--root", default=".", help="kit root (default: the working directory)")
    args = parser.parse_args()
    if args.check == args.write:
        parser.error("choose exactly one of --check or --write")
    root = Path(args.root).resolve()
    pairing = load_pairing(root)
    anchors = anchor_paths(root, pairing)
    scope = set(anchors)
    prefix = discover_prefix(root)
    selected = select_anchors(args.anchors, anchors)
    violations: list[str] = []
    if args.write:
        for anchor in selected:
            record_rel = anchor[: -len(".md")] + RECORD_SUFFIX
            zh_rel = anchor[: -len(".md")] + ZH_SUFFIX
            if not (root / anchor).is_file() or not (root / zh_rel).is_file():
                print(f"pair-docs: {anchor}: cannot record, {zh_rel} is missing", file=sys.stderr)
                return 1
            targets = pairing["hostCanonical"] if anchor in pairing["hostCanonical"] else []
            current = compute_record(prefix, anchor, root, scope, targets)
            canonical = render_record(anchor, current, anchor in pairing["hostCanonical"], prefix)
            (root / record_rel).write_text(canonical, encoding="utf-8")
            print(f"pair-docs: wrote {record_rel}")
        return 0
    for anchor in selected:
        violations.extend(check_anchor(root, prefix, anchor, scope, pairing))
    known = set(selected)
    listed = _ENGINE.corpus(root)
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".md", ".yaml"}:
            continue
        relative = path.relative_to(root).as_posix()
        if listed is not None and relative not in listed:
            continue
        if relative.endswith(RECORD_SUFFIX):
            anchor = relative[: -len(RECORD_SUFFIX)] + ".md"
        elif relative.endswith(ZH_SUFFIX):
            anchor = relative[: -len(ZH_SUFFIX)] + ".md"
        else:
            continue
        if anchor not in known and anchor not in scope:
            violations.append(f"{relative}: orphan pairing file — {anchor} is not an in-scope pair")
    if violations:
        print("pair-docs: pairing violations:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print(f"pair-docs: {len(selected)} pair(s) in scope, all complete and in step.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
