"""Helpers every check shares: configuration, file iteration, line and word counts."""

from __future__ import annotations

from fnmatch import fnmatch

import os

from pathlib import Path
from typing import Iterator
from typing import NoReturn
import json
import subprocess
import sys

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "workflow.json"  # tools/workflow.json, beside the package


MAX_FILE_BYTES = 2 * 1024 * 1024


SKIP_DIRECTORIES = {
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", "node_modules",
    "dist", "build", "target", ".next", ".cache", ".mypy_cache", ".pytest_cache",
    "vendor", "coverage",
}

# capability: check-engine — the declared check kinds are this capability's interface.
# region: supported-kinds
SUPPORTED_KINDS = ("tier-manifest", "budget", "required-sections", "source-mirror", "capability-registry", "criteria-traced", "forbidden-regex", "note-class", "link-target", "evidence-record", "skill-trigger", "publish-manifest", "sealed-manifest")
# endregion: supported-kinds


def die(message: str, code: int = 2, prog: str = "check-invariants") -> NoReturn:
    """Print a diagnostic to stderr under the calling program's name, and exit."""
    print(f"{prog}: {message}", file=sys.stderr)
    raise SystemExit(code)


def git(*args: str, cwd: Path | None = None, prog: str = "check-invariants") -> subprocess.CompletedProcess[str]:
    """Run git and capture its output, with the caller's program name in a failure."""
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=False, cwd=cwd)
    except FileNotFoundError:
        die("git is not on PATH", prog=prog)


def resolve_merge_base(base: str, head: str, cwd: Path | None = None, prog: str = "check-invariants") -> str:
    """Return the merge base, refusing to guess when it does not exist locally."""
    result = git("merge-base", base, head, cwd=cwd, prog=prog)
    if result.returncode != 0:
        detail = result.stderr.strip() or "no output"
        die(
            f"cannot resolve the merge base of {base!r} and {head!r}: {detail}\n"
            "  This tool never fetches and never guesses a base. Fetch the ref first, or pass\n"
            "  the exact ref your change is based on, for example --base origin/main."
        , prog=prog)
    return result.stdout.strip()


def committed_changes(merge_base: str, head: str, cwd: Path | None = None, prog: str = "check-invariants") -> list[tuple[str, str]]:
    """Return (status, path) for every path that differs between the base and the head."""
    result = git("diff", "--name-status", "--find-renames", merge_base, head, cwd=cwd, prog=prog)
    if result.returncode != 0:
        die(f"git diff failed: {result.stderr.strip()}", prog=prog)
    changes: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        # A rename prints "R100<TAB>old<TAB>new"; the new path is the one that matters.
        changes.append((fields[0], fields[-1]))
    return changes


def worktree_changes(cwd: Path | None = None, prog: str = "check-invariants") -> list[tuple[str, str]]:
    """Return (status, path) for staged, unstaged, and untracked paths."""
    # --untracked-files=all lists each new file instead of collapsing a new
    # directory into one "dir/" entry, so a declared pattern can match it.
    result = git("status", "--porcelain", "--untracked-files=all", cwd=cwd, prog=prog)
    if result.returncode != 0:
        die(f"git status failed: {result.stderr.strip()}", prog=prog)
    changes: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2].strip() or "??"
        path = line[3:].strip()
        if " -> " in path:  # a worktree rename prints "old -> new"
            path = path.rsplit(" -> ", 1)[1]
        changes.append((status, path))
    return changes


def git_root(cwd: Path | None = None, prog: str = "check-invariants") -> Path:
    """Return the working tree root, or exit when this is not a git repository."""
    result = git("rev-parse", "--show-toplevel", cwd=cwd, prog=prog)
    if result.returncode != 0:
        die("not a git repository", prog=prog)
    return Path(result.stdout.strip())


def load_surfaces(path: Path, prog: str = "check-invariants") -> dict:
    """Read and validate the surface map every tool reads: one loader, one set of rules."""
    if not path.is_file():
        die(f"config not found: {path}", prog=prog)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read config {path}: {exc}", prog=prog)
    if not isinstance(config, dict):
        die(f"config must be a JSON object: {path}", prog=prog)
    surfaces = config.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        die(f"config needs a non-empty 'surfaces' list: {path}", prog=prog)
    for surface in surfaces:
        if not isinstance(surface, dict) or not surface.get("name") or not surface.get("patterns"):
            die(f"every surface needs a non-empty 'name' and 'patterns': {path}", prog=prog)
        exclude = surface.get("exclude")
        if exclude is not None and (not isinstance(exclude, list) or not all(isinstance(item, str) and item for item in exclude)):
            die(f"surface {surface['name']!r}: 'exclude' must be a list of non-empty patterns when present: {path}", prog=prog)
    return config


def changed_paths(base: str | None, head: str = "HEAD", worktree_only: bool = False,
                  cwd: Path | None = None, prog: str = "check-invariants") -> tuple[list[str], str | None]:
    """Return the changed paths against `base`, and the merge base they were resolved from."""
    paths: list[str] = []
    merge_base: str | None = None
    if not worktree_only:
        if not base:
            die("--base is required unless --worktree-only is given", prog=prog)
        merge_base = resolve_merge_base(base, head, cwd=cwd, prog=prog)
        for _, path in committed_changes(merge_base, head, cwd=cwd, prog=prog):
            if path not in paths:
                paths.append(path)
    for _, path in worktree_changes(cwd=cwd, prog=prog):
        if path not in paths:
            paths.append(path)
    return sorted(paths), merge_base


def matches(path: str, patterns: list[str]) -> bool:
    """Return True when one of these fnmatch patterns claims the path."""
    return any(fnmatch(path, pattern) for pattern in patterns)


def select_checks(config: dict, paths: list[str]) -> list[dict]:
    """Return the declared checks whose patterns read at least one of these paths.

    This is the iteration mode: one edited page is read by a handful of checks, not by all
    of them. It is deliberately not the evidence a delivery needs — that comes from the
    surface map, because a guard's own test is a surface, not a pattern a check declares.
    An empty result means no declared check reads these paths, which the caller reports
    rather than letting it read as a pass.
    """
    selected: list[dict] = []
    for check in config["checks"]:
        exclude = check.get("exclude", [])
        if any(matches(path, check.get("patterns", [])) and not matches(path, exclude) for path in paths):
            selected.append(check)
    return selected


def load_config(path: Path) -> dict:
    """Read and validate the check declarations."""
    if not path.is_file():
        die(f"config not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read config {path}: {exc}")
    if not isinstance(config, dict):
        die(f"config must be a JSON object: {path}")
    checks = config.get("checks")
    if not isinstance(checks, list) or not checks:
        die(f"config needs a non-empty 'checks' list: {path}")
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            die(f"check #{index} is not a JSON object: {path}")
        if not check.get("id"):
            die(f"check #{index} has no 'id': {path}")
        if check.get("kind") not in SUPPORTED_KINDS:
            die(f"check {check['id']!r} has unsupported kind {check.get('kind')!r}; supported: {', '.join(SUPPORTED_KINDS)}")
        if not check.get("patterns"):
            die(f"check {check['id']!r} needs a non-empty 'patterns' list: {path}")
    return config


def corpus(root: Path) -> set[str] | None:
    """The repository's files as git sees them: tracked, plus untracked and not ignored.

    A file git ignores is not in the repository, so it is not a check's subject either.
    Without this, every project has to declare its own scratch directories — in the
    publication switch and in the pairing exclusions, one entry per directory per config —
    and a declaration a project forgets turns a check red on a file that cannot ship.
    Returns None when git cannot answer, and the caller walks the tree instead.
    """
    try:
        listed = subprocess.run(("git", "ls-files", "-co", "--exclude-standard", "-z"),
                                cwd=root, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return {name for name in listed.stdout.decode("utf-8", "surrogateescape").split("\0") if name}


def iter_files(root: Path, patterns: list[str]) -> Iterator[tuple[str, str]]:
    """Yield (repository-relative path, text) for every text file matching one pattern.

    The corpus is the repository rather than the disk. When git cannot answer, the walk is
    the fallback and `SKIP_DIRECTORIES` covers the directories that are never source; that
    list is a guess at what git already knows.
    """
    listed = corpus(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRECTORIES)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            relative = path.relative_to(root).as_posix()
            if listed is not None and relative not in listed:
                continue
            if not any(fnmatch(relative, pattern) for pattern in patterns):
                continue
            if path.is_symlink():
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            yield relative, text


def section_body(text: str, section: str) -> list[str]:
    """Return the lines between `section` and the next '## ' heading."""
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line == section
            continue
        if inside:
            body.append(line)
    return body


def count_lines(text: str) -> int:
    """Count physical lines, treating a missing trailing newline as a line."""
    if text == "":
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def count_words(text: str) -> int:
    """Count whitespace-separated tokens, the same unit as `wc -w`.

    The unit undercounts prose written without word-separating spaces, so a
    Chinese standing document stays on a line budget; see docs/documentation.md.
    """
    return len(text.split())


def read_manifest(root: Path, check: dict, key: str) -> dict:
    """Read a check's manifest and require `key` to be a non-empty list."""
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
    values = manifest.get(key)
    if not isinstance(values, list) or not values:
        die(f"check {check['id']!r}: manifest {manifest_rel} needs a non-empty {key!r} list")
    return manifest
