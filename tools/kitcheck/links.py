"""The link-target guard: a relative link in an agent-loaded file must name a real file."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .core import iter_files

_PAIR_DOCS = "pair_docs"


def _load_pair_docs():
    """Load tools/pair-docs.py by path, so "what counts as a link" has exactly one home.

    The pairing tool already resolves every relative link in a paired document, and a
    second scanner here would drift from it. The engine loads that script rather than
    importing it, because its name is not a module identifier — the same reason every
    tool loads this package by path.
    """
    if _PAIR_DOCS not in sys.modules:
        script = Path(__file__).resolve().parent.parent / "pair-docs.py"
        spec = importlib.util.spec_from_file_location(_PAIR_DOCS, script)
        if spec is None or spec.loader is None:
            raise SystemExit("tools/pair-docs.py is missing")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_PAIR_DOCS] = module
        spec.loader.exec_module(module)
    return sys.modules[_PAIR_DOCS]


def check_link_target(root: Path, check: dict) -> list[str]:
    """Reject a relative link that names no file, in the files an agent loads but never pairs.

    `pair-docs` checks the links inside paired documents. Agent instructions are excluded
    from pairing because they are single-language by rule, so without this check a rule,
    a skill, or a record template can cite a file that does not exist and no gate goes
    red: the reader follows it and finds nothing. A link into a stage the tier switch
    declares absent is exempt, which is the same tolerance `pair-docs` applies.
    """
    module = _load_pair_docs()
    violations: list[str] = []
    for relative, text in iter_files(root, check["patterns"]):
        violations.extend(module.link_violations(root, relative, text))
    return violations


SELF_TEST_CASES = (
    (
        "link-target",
        {"id": "self-link-target", "kind": "link-target", "patterns": ["*.md"]},
        {"AGENTS.md": "# A\n\nRead [the map](docs/gone.md) first.\n"},
        {"AGENTS.md": "# A\n\nRead [the map](docs/here.md) first.\n", "docs/here.md": "# Map\n"},
    ),
)
