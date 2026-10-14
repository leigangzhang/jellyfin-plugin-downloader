"""Write or verify this kit's bilingual-pair consistency records.

The command line: `--check` recomputes every record from the two documents and reports
a missing sibling, an absent language switcher, a fenced code block that differs
between the sides, or a section whose recorded hash no longer matches its content;
`--write` rewrites the records. Scope, exclusions, and the anchors whose records must
stay byte-identical to a host repository's renderer come from `pairing` in
`tools/workflow.json`, this kit's single configuration file.
"""

from __future__ import annotations

import argparse
import posixpath
import sys
from pathlib import Path

from .checks import (
    link_locale_violations,
    link_violations,
    record_diff,
    structure_violations,
    switcher_target_violations,
)
from .model import (
    RECORD_SUFFIX,
    SWITCHER_EN,
    SWITCHER_ZH,
    ZH_SUFFIX,
    _ENGINE,
    anchor_paths,
    die,
    discover_prefix,
    fenced_blocks,
    load_engine,
    load_pairing,
)
from .render import compute_record, parse_record, render_record, switcher_line


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
