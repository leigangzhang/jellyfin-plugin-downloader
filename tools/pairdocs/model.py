"""The pairing model: discovery, Markdown block boundaries, and link resolution.

Every rule here answers one question about text: which documents are in scope, where a
top-level block starts and ends, which sections a document carries, and what a relative
link resolves to. The checks, the record renderer, and the command line all read the
model through this module, so "what counts as a section" has exactly one home.
"""

from __future__ import annotations

import importlib.util
import json
import posixpath
import re
import sys
from pathlib import Path


def load_engine():
    """Load tools/kitcheck by path, so the tier switch resolves identically for every tool.

    A plain `import kitcheck` resolves only while `tools/` happens to sit on `sys.path`:
    the script's own directory when this file is run, or a loader that already registered
    the package. The suite loads the entry point by path from the repository root, so the
    package is loaded here rather than assumed.
    """
    if "kitcheck" not in sys.modules:
        package = Path(__file__).resolve().parent.parent / "kitcheck"
        spec = importlib.util.spec_from_file_location(
            "kitcheck", package / "__init__.py", submodule_search_locations=[str(package)])
        if spec is None or spec.loader is None:
            raise SystemExit("tools/kitcheck is missing")
        module = importlib.util.module_from_spec(spec)
        sys.modules["kitcheck"] = module
        spec.loader.exec_module(module)
    return sys.modules["kitcheck"]


_ENGINE = load_engine()

HEADING_LINE = re.compile(r"^#{1,6} .+$", re.MULTILINE)
HTML_ID = re.compile(r'<a\s+id="([^"]+)"')
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


def _append_section_block(result: list[dict], line: str, raw: str) -> None:
    """File one top-level block under the heading it follows.

    A heading opens a section; a fenced block is dropped, because its content is
    compared on its own; anything else joins the section already open. A paragraph
    block starts at its first non-space character, so a one-to-three-space indent is
    layout, not content.
    """
    if ATX.match(line):
        result.append({"heading": line, "blocks": [raw]})
        return
    if FENCE.match(line):
        return
    if not LIST_ITEM.match(line):
        first, _, rest = raw.partition("\n")
        raw = first.lstrip(" ") + (("\n" + rest) if rest else "")
    result[-1]["blocks"].append(raw)


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
        _append_section_block(result, line, "\n".join(lines[index : end + 1]))
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
