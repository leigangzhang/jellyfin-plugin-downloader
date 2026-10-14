"""The pairing checks: every way a pair can be complete on paper and still wrong.

A check here returns a list of human-readable violations and writes nothing. The rules
are the ones the kit documents in docs/i18n.md: a link that names no file, a link into
the wrong side of a pair, a counterpart that lost a row or an item, a switcher that
points at the page it sits on, and a recorded hash that no longer matches the text.
"""

from __future__ import annotations

import posixpath
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

from .model import (
    SWITCHER_EN,
    SWITCHER_ZH,
    ZH_SUFFIX,
    _ENGINE,
    document_links,
    repo_path,
    resolve_pair,
)
from .render import document_anchors, structure_signature, switcher_line


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
