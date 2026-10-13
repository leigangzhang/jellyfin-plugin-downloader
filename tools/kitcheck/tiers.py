"""The tier switch: a project's declared tiers, and the check that proves the tree agrees."""

from __future__ import annotations

import json
import re

from .core import iter_files

TIER_SWITCH = "tools/tiers.json"


def load_tier_switch(root: Path) -> dict:
    """Resolve the tier switch into one install state per stage.

    A stage's tier is its entry in a lifecycle's `stages`, else that lifecycle's
    `default`, else the top-level `default`. A stage is installed when some lifecycle
    resolves it at or above the tier the ladder introduces it at: one tree serves every
    lifecycle, so the most generous resolution wins and `none` everywhere means absent.
    """
    path = root / TIER_SWITCH
    if not path.exists():
        return {"stages": {}, "installed": set(), "absent_homes": [], "declared": False}
    switch = json.loads(path.read_text(encoding="utf-8"))
    tiers = list(switch.get("tiers", []))
    ladder = switch.get("ladder", {})
    catalogue = switch.get("stages", {})
    introduced = {stage: tier for tier, names in ladder.items() for stage in names}
    lifecycles = switch.get("lifecycles", {})
    installed: set[str] = set()
    for stage in catalogue:
        for name, lifecycle in lifecycles.items():
            tier = (lifecycle.get("stages") or {}).get(stage) or lifecycle.get("default") or switch.get("default")
            if tier != "none" and tier in tiers and tiers.index(tier) >= tiers.index(introduced.get(stage, tiers[0])):
                installed.add(stage)
                break
    def homes_of(stage: str) -> list[str]:
        home = catalogue[stage].get("home")
        return [home] if isinstance(home, str) else list(home or [])

    absent = [home for s in catalogue if s not in installed for home in homes_of(s)]
    return {"stages": catalogue, "ladder": ladder, "tiers": tiers, "introduced": introduced,
            "installed": installed, "absent": sorted(set(catalogue) - installed),
            "absent_homes": sorted(absent), "declared": True, "switch": switch}


def literal_prefix(pattern: str) -> str:
    """The part of a glob before its first wildcard: what the pattern actually pins."""
    for index, char in enumerate(pattern):
        if char in "*?[":
            return pattern[:index]
    return pattern


def home_owns(pattern: str, home: str) -> bool:
    """True when a check's pattern can only match files inside one stage's home.

    A home with no literal prefix — `*.spec.*`, `*_test.py` — cannot be reached by prefix
    comparison at all, so a check that declares the identical glob is accepted as its
    guard: the check and the stage name the same set of files.
    """
    if pattern == home:
        return True
    pinned, owned = literal_prefix(pattern), literal_prefix(home)
    return bool(owned) and (pinned == owned or pinned.startswith(owned.rstrip("/") + "/") or pinned.startswith(owned))


def homes_of(catalogue: dict, stage: str) -> list[str]:
    """Every home a stage owns: one glob, or a list of them."""
    home = catalogue.get(stage, {}).get("home")
    return [home] if isinstance(home, str) else list(home or [])


def check_tier_manifest(root: Path, check: dict) -> list[str]:
    """Prove the tier switch, the tree, and the map's tier table agree."""
    switch = load_tier_switch(root)
    if not switch["declared"]:
        return [f"{TIER_SWITCH} is missing: without the switch a tier is declared in prose only"]
    violations: list[str] = []
    tiers, ladder, catalogue = switch["tiers"], switch["ladder"], switch["stages"]
    if not tiers or not ladder or not catalogue:
        return [f"{TIER_SWITCH}: `tiers`, `ladder`, and `stages` must all be non-empty"]
    for stage in catalogue:
        if stage not in switch["introduced"]:
            violations.append(f"{TIER_SWITCH}: stage {stage!r} is in no ladder entry, so no tier introduces it")
    for tier, names in ladder.items():
        if tier not in tiers:
            violations.append(f"{TIER_SWITCH}: ladder entry {tier!r} is not in `tiers`")
        for stage in names:
            if stage not in catalogue:
                violations.append(f"{TIER_SWITCH}: ladder entry {tier!r} names undeclared stage {stage!r}")
    for lifecycle, body in (switch["switch"].get("lifecycles") or {}).items():
        for stage, tier in (body.get("stages") or {}).items():
            if stage not in catalogue:
                violations.append(f"{TIER_SWITCH}: lifecycle {lifecycle!r} re-tiers undeclared stage {stage!r}")
            if tier != "none" and tier not in tiers:
                violations.append(f"{TIER_SWITCH}: lifecycle {lifecycle!r} gives {stage!r} unknown tier {tier!r}")
    # The tree must agree with the switch, in both directions.
    for stage in switch["absent"]:
        for home in homes_of(catalogue, stage):
            if next(iter_files(root, [home]), None) is not None:
                violations.append(f"{TIER_SWITCH}: stage {stage!r} resolves to `none`, but {home!r} still holds files")
    for stage in sorted(switch["installed"]):
        homes = homes_of(catalogue, stage)
        if not homes:
            continue
        # An installed stage must own something somewhere, and every path it owns must be
        # guarded. It need not hold a file under each glob: a stage that owns `tests/*`
        # and `test_*.py` is installed when either one is.
        if not any(next(iter_files(root, [home]), None) is not None for home in homes):
            violations.append(f"{TIER_SWITCH}: stage {stage!r} is installed, but none of {homes!r} matches a file")
    # The map's tier table is the human rendering of the ladder.
    map_text = (root / "dev/README.md").read_text(encoding="utf-8") if (root / "dev/README.md").exists() else ""
    for tier in tiers:
        row = next((line for line in map_text.split("\n") if line.startswith(f"| **{tier.capitalize()}") or line.startswith(f"| **{tier}")), "")
        if not row:
            violations.append(f"dev/README.md: the tier table has no row for {tier!r}, so the table cannot agree with the switch")
            continue
        expected = sorted(catalogue[s]["number"] for s in ladder[tier] if catalogue[s].get("number") is not None)
        found = sorted(int(n) for n in re.findall(r"\b(\d{1,2})\b", row))
        if found != expected:
            violations.append(f"dev/README.md: the {tier!r} row lists stations {found}, but the switch introduces {expected}")
        for stage in ladder[tier]:
            label = catalogue[stage].get("label")
            if catalogue[stage].get("number") is None and label and label not in row:
                violations.append(f"dev/README.md: the {tier!r} row does not name {stage!r} ({label!r}), which the switch introduces there")
    return violations


def check_stage_ownership(root: Path, checks: list[dict]) -> list[str]:
    """Reject an installed stage whose home no declared check reaches into.

    The switch and the tree can agree while the guard is missing: a stage installed, its
    home holding files, and no check whose patterns reach into that home is a stage
    nothing verifies. The rule needs both halves of the configuration at once, so
    `run_checks` calls it with the checks it already loaded — a runner sees one check and
    can only ever compare it with itself.
    """
    switch = load_tier_switch(root)
    catalogue = switch["stages"]
    violations: list[str] = []
    for stage in sorted(switch["installed"]):
        for home in homes_of(catalogue, stage):
            owners = [c["id"] for c in checks if any(home_owns(p, home) for p in c.get("patterns", []))]
            if not owners:
                violations.append(f"{TIER_SWITCH}: stage {stage!r} is installed, but no check owns {home!r}")
    return violations


SELF_TEST_TIER_SWITCH = {
    "comment": "fixture",
    "default": "long-lived",
    "tiers": ["minimum", "long-lived"],
    "ladder": {"minimum": ["proposal"], "long-lived": ["contract"]},
    "stages": {
        "proposal": {"number": 2, "label": "Proposal", "home": ".agents/notes/proposed/*.md"},
        "contract": {"number": 4, "label": "Contract", "home": "dev/contracts/*.md"},
    },
    "lifecycles": {"fixture": {"default": "long-lived", "stages": {}}},
}
SELF_TEST_TIER_TABLE_OK = "| Tier | Stations |\n|---|---|\n| **Minimum** | 2 |\n| **Long-lived** | Everything + 4 |\n"
SELF_TEST_TIER_TABLE_BAD = "| Tier | Stations |\n|---|---|\n| **Minimum** | 2 |\n| **Long-lived** | Everything + 9 |\n"
SELF_TEST_TIER_FILES = {
    "tools/tiers.json": json.dumps(SELF_TEST_TIER_SWITCH),
    "dev/README.md": SELF_TEST_TIER_TABLE_OK,
    ".agents/notes/proposed/2026-01-01-a.md": "# A\n",
    "dev/contracts/2026-01-01-b.md": "# B\n",
}
SELF_TEST_TIER_FILES_NONE_WITH_FILES = dict(SELF_TEST_TIER_FILES)
SELF_TEST_TIER_FILES_NONE_WITH_FILES["tools/tiers.json"] = json.dumps(
    {**SELF_TEST_TIER_SWITCH, "lifecycles": {"fixture": {"default": "minimum", "stages": {"contract": "none"}}}})
SELF_TEST_TIER_FILES_BAD_TABLE = dict(SELF_TEST_TIER_FILES)
SELF_TEST_TIER_FILES_BAD_TABLE["dev/README.md"] = SELF_TEST_TIER_TABLE_BAD


SELF_TEST_CASES = (
    ("tier-manifest", {"id": "tier-manifest", "kind": "tier-manifest", "patterns": ["tools/tiers.json"], "why": "fixture"},
     SELF_TEST_TIER_FILES_NONE_WITH_FILES, SELF_TEST_TIER_FILES),
)
