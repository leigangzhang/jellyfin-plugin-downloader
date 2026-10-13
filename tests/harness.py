"""Shared fixtures for the kit's contract tests: the root, the loaders, and one heading reader."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = json.loads((ROOT / "tools" / "workflow.json").read_text(encoding="utf-8"))

_CHECKER = None


def load_run_evidence():
    """Load tools/run-evidence.py, whose name is not a valid module identifier."""
    spec = importlib.util.spec_from_file_location("run_evidence", ROOT / "tools" / "run-evidence.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("tools/run-evidence.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_pair_docs():
    """Load tools/pair-docs.py, whose name is not a valid module identifier."""
    spec = importlib.util.spec_from_file_location("pair_docs", ROOT / "tools" / "pair-docs.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("tools/pair-docs.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_checker():
    """Load the check engine package by path, the way every tool in tools/ is loaded.

    A plain `import kitcheck` would work when the suite is run from the repository
    root, but it is unresolvable to a reader and to an editor: the package lives in
    tools/, which is only on the path because Python puts a script's directory there.
    """
    global _CHECKER
    if _CHECKER is None:
        package = ROOT / "tools" / "kitcheck"
        spec = importlib.util.spec_from_file_location("kitcheck", package / "__init__.py",
                                                     submodule_search_locations=[str(package)])
        if spec is None or spec.loader is None:
            raise RuntimeError("tools/kitcheck is missing")
        module = importlib.util.module_from_spec(spec)
        sys.modules["kitcheck"] = module
        spec.loader.exec_module(module)
        _CHECKER = module
    return _CHECKER


def sections(path: Path) -> list[str]:
    """Return every level-two heading in a Markdown file, in order."""
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## ")]


def stage_installed(name: str) -> bool:
    """Whether the tier switch installs one stage, so a corpus test can skip an absent one.

    The real-tree tests run against whatever tier the project chose. A test that reads a
    stage's home must not fail for a stage the switch declares absent, or every project
    adopting a lower tier inherits a red suite.
    """
    return name in load_checker().load_tier_switch(ROOT)["installed"]
