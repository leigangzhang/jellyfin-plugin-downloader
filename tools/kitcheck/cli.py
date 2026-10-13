"""The command line: load the configuration, run every declared check, report, and exit."""

from __future__ import annotations

from pathlib import Path
import argparse

from .core import DEFAULT_CONFIG, changed_paths, die, load_config, select_checks
from .registry import run_checks, self_test

def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="check-invariants",
        description="Run the executable conventions declared in the workflow config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("root", nargs="?", default=".", help="tree to scan (default: the current directory)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="check declarations (default: tools/workflow.json)")
    parser.add_argument("--self-test", action="store_true", help="prove each check kind can fail, using throwaway fixtures")
    parser.add_argument("--list", action="store_true", help="list the declared checks and exit")
    parser.add_argument("--changed", metavar="BASE", help="run only the checks that read the paths changed against BASE (iteration mode; the full set is the boundary run)")
    parser.add_argument("--worktree-only", action="store_true", help="with --changed: ignore committed changes and read the worktree")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    config = load_config(args.config)

    root = Path(args.root).resolve()
    if not root.is_dir():
        die(f"not a directory: {root}")

    checks = config["checks"]
    if args.changed:
        paths, merge_base = changed_paths(args.changed, worktree_only=args.worktree_only, cwd=root, prog="check-invariants")
        checks = select_checks(config, paths)
        scope = "the worktree" if args.worktree_only else f"{args.changed} (merge base {merge_base[:12]})"
        print(f"check-invariants: {len(paths)} changed path(s) against {scope}; "
              f"{len(checks)} of {len(config['checks'])} declared check(s) read them")
        if not checks:
            print("check-invariants: no declared check reads these paths; the full set is the delivery-boundary run")
            return 0

    if args.list:
        for check in checks:
            print(f"  {check['id']}  ({check['kind']})  {check.get('why', '')}")
        return 0

    failed = False
    for check_id, violations in run_checks(root, checks):
        if violations:
            failed = True
            print(f"FAIL {check_id}")
            for violation in violations:
                print(f"  {violation}")
        else:
            print(f"PASS {check_id}")
    return 1 if failed else 0
