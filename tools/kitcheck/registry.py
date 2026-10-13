"""The registry: every supported kind, its runner, and the self-test that proves it can fail."""

from __future__ import annotations

from .capabilities import SELF_CAPABILITY_CHECK, SELF_CAPABILITY_FILES
from .mirrors import SELF_MIRROR_MANIFEST
from .criteria import SELF_CRITERIA_CHECK, SELF_CRITERIA_FILES
from .seals import SELF_SEALED_CHECK, SELF_SEALED_TEXT

from pathlib import Path
import hashlib
import json
import sys
import tempfile

from . import capabilities, criteria, evidence, links, mirrors, policies, publication, records, seals, skills, tiers
from .capabilities import check_capability_registry
from .core import SUPPORTED_KINDS, die, iter_files, load_config
from .evidence import check_evidence_record
from .links import check_link_target
from .mirrors import check_source_mirror
from .policies import check_budget, check_forbidden_regex, check_required_sections
from .criteria import check_criteria_traced
from .publication import check_publish_manifest
from .records import check_note_class
from .seals import check_sealed_manifest
from .skills import check_skill_trigger
from .tiers import check_stage_ownership, check_tier_manifest, home_owns, load_tier_switch



RUNNERS = {
    "tier-manifest": check_tier_manifest,
    "budget": check_budget,
    "required-sections": check_required_sections,
    "forbidden-regex": check_forbidden_regex,
    "note-class": check_note_class,
    "link-target": check_link_target,
    "evidence-record": check_evidence_record,
    "source-mirror": check_source_mirror,
    "capability-registry": check_capability_registry,
    "criteria-traced": check_criteria_traced,
    "skill-trigger": check_skill_trigger,
    "publish-manifest": check_publish_manifest,
    "sealed-manifest": check_sealed_manifest,
}


def run_checks(root: Path, checks: list[dict]) -> list[tuple[str, list[str]]]:
    """Run every declared check and return (id, violations) in declaration order.

    A check whose patterns match no file is reported as a violation. A check with no
    subject cannot fail, so it is an empty shell rather than a guard: it reads as
    protection while proving nothing.

    `tier-manifest` also carries the rule that every installed stage's home must be
    reachable by some declared check. That rule needs the switch and the whole check list
    at once, so it is applied here rather than inside the runner, which sees one check.
    """
    switch = load_tier_switch(root)
    results: list[tuple[str, list[str]]] = []
    for check in checks:
        # A check that can only match a stage the switch says is absent is a declared
        # absence, not an empty shell: it is skipped, together with the empty-corpus rule.
        if switch["absent_homes"] and check["patterns"] and all(
                any(home_owns(pattern, home) for home in switch["absent_homes"]) for pattern in check["patterns"]):
            results.append((check["id"], []))
            continue
        matched = sum(1 for _ in iter_files(root, check["patterns"]))
        violations = RUNNERS[check["kind"]](root, check)
        if check["kind"] == "tier-manifest":
            violations = violations + check_stage_ownership(root, checks)
        if matched == 0 and check["kind"] != "tier-manifest":
            violations = [f"no file matched {check['patterns']!r}: a check with no subject cannot fail"] + violations
        results.append((check["id"], violations))
    return results


def write_fixtures(files: dict[str, str]) -> "tempfile.TemporaryDirectory[str]":
    """Create a throwaway tree containing the given relative paths."""
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory


def self_test() -> int:
    """Prove every check kind rejects an invalid fixture and accepts a valid one."""
    failed: list[str] = []
    for kind, check, invalid_files, valid_files in SELF_TEST_CASES:
        with write_fixtures(invalid_files) as invalid_dir:
            rejected = RUNNERS[kind](Path(invalid_dir), check)
        with write_fixtures(valid_files) as valid_dir:
            accepted = RUNNERS[kind](Path(valid_dir), check)

        if not rejected:
            failed.append(kind)
            print(f"FAIL {kind}: an invalid fixture was accepted, so this check cannot fail")
        elif accepted:
            failed.append(kind)
            print(f"FAIL {kind}: a valid fixture was rejected, so this check is too strict: {accepted[0]}")
        else:
            print(f"PASS {kind}: the invalid fixture was rejected ({rejected[0]}), the valid one was accepted")

    # A kind with no case above, a supported kind with no runner, or a runner nothing
    # declares would leave --self-test green while that kind was never proven to fail.
    # Comparing the three registries is the negative control for "every guard has one".
    covered = {case[0] for case in SELF_TEST_CASES}
    uncovered = sorted(kind for kind in RUNNERS if kind not in covered)
    unrunnable = sorted(kind for kind in SUPPORTED_KINDS if kind not in RUNNERS)
    undeclared = sorted(kind for kind in RUNNERS if kind not in SUPPORTED_KINDS)
    if uncovered or unrunnable or undeclared:
        failed.append("kind-coverage")
        reasons = []
        if uncovered:
            reasons.append(f"kind(s) with no negative-control case: {', '.join(uncovered)}")
        if unrunnable:
            reasons.append(f"declared kind(s) with no runner: {', '.join(unrunnable)}")
        if undeclared:
            reasons.append(f"runner(s) no check can declare: {', '.join(undeclared)}")
        print(f"FAIL kind-coverage: {'; '.join(reasons)}")
    else:
        print(f"PASS kind-coverage: all {len(RUNNERS)} kinds have a runner and a negative-control case")

    # A check with no subject must be rejected as well, or it passes vacuously.
    with write_fixtures({"unrelated.txt": "nothing here matches the check\n"}) as empty_dir:
        empty = run_checks(Path(empty_dir), [
            {"id": "self-empty", "kind": "budget", "patterns": ["AGENTS.md"], "maxLines": 10},
        ])
    if empty and empty[0][1]:
        print(f"PASS empty-corpus: a check matching no file was rejected ({empty[0][1][0]})")
    else:
        failed.append("empty-corpus")
        print("FAIL empty-corpus: a check matching no file passed, so it cannot fail")

    # An installed stage whose home no check reaches into must be rejected: the switch and
    # the tree can agree while the guard is missing entirely. Both directions need a probe,
    # or the rule is one nobody has watched fail.
    tier_check = {"id": "tier-manifest", "kind": "tier-manifest", "patterns": ["tools/tiers.json"]}
    with write_fixtures(tiers.SELF_TEST_TIER_FILES) as unguarded_dir:
        unguarded = run_checks(Path(unguarded_dir), [tier_check])
    missing_owner = [v for _id, vs in unguarded for v in vs if "no check owns" in v]
    if missing_owner:
        print(f"PASS tier-ownership: an installed stage with no owning check was rejected ({missing_owner[0]})")
    else:
        failed.append("tier-ownership")
        print("FAIL tier-ownership: an installed stage with no owning check passed")
    with write_fixtures(tiers.SELF_TEST_TIER_FILES) as guarded_dir:
        guarded = run_checks(Path(guarded_dir), [
            tier_check,
            {"id": "owns-proposal", "kind": "budget", "patterns": [".agents/notes/proposed/*.md"], "maxLines": 100},
            {"id": "owns-contract", "kind": "budget", "patterns": ["dev/contracts/*.md"], "maxLines": 100},
        ])
    still_missing = [v for _id, vs in guarded for v in vs if "no check owns" in v]
    if not still_missing:
        print("PASS tier-ownership-guarded: a stage with an owning check was accepted")
    else:
        failed.append("tier-ownership-guarded")
        print(f"FAIL tier-ownership-guarded: a stage with an owning check was rejected ({still_missing[0]})")

    # A section may declare several accepted spellings, because a translated page
    # carries its own heading wording. Both directions need a probe: the localized
    # spelling satisfies the requirement, and a section present in no spelling fails.
    localized_check = {
        "id": "self-sections-localized",
        "kind": "required-sections",
        "patterns": [".agents/notes/*.md"],
        "sections": ["## Problem", ["## Alternatives considered", "## 曾考虑的替代方案"]],
        "minBodyLines": 1,
    }
    with write_fixtures({
        ".agents/notes/decision.md": "## Problem\nsomething broke\n\n## 曾考虑的替代方案\n\n**Do nothing.** It stays broken.\n\n## Decision\nfixed\n",
    }) as localized_dir:
        rejected_localized = RUNNERS["required-sections"](Path(localized_dir), localized_check)
    if not rejected_localized:
        print("PASS required-sections-localized: a declared translated spelling satisfied its section")
    else:
        failed.append("required-sections-localized")
        print(f"FAIL required-sections-localized: an accepted spelling was rejected ({rejected_localized[0]})")

    with write_fixtures({
        ".agents/notes/decision.md": "## Problem\nsomething broke\n\n## Decision\nfixed\n",
    }) as missing_alias_dir:
        missing_alias = RUNNERS["required-sections"](Path(missing_alias_dir), localized_check)
    if missing_alias:
        print(f"PASS required-sections-missing-alias: a section absent in every spelling was rejected ({missing_alias[0]})")
    else:
        failed.append("required-sections-missing-alias")
        print("FAIL required-sections-missing-alias: a missing section passed because another spelling was declared")

    # A mirror must also fail when its source region markers are gone, not only
    # when the pasted text drifts; otherwise deleting the markers disables it.
    with write_fixtures({
        "contracts/mirrors.json": json.dumps(SELF_MIRROR_MANIFEST),
        "contracts/a.md": "# A\n\n```text mirror\nkeep = 2\n```\n",
        "src/a.py": "keep = 2\n",
    }) as marker_dir:
        missing = RUNNERS["source-mirror"](Path(marker_dir), {
            "id": "self-mirror-markers",
            "kind": "source-mirror",
            "patterns": ["contracts/*.md"],
            "manifest": "contracts/mirrors.json",
        })
    if missing:
        print(f"PASS source-mirror-markers: a missing source region marker was rejected ({missing[0]})")
    else:
        failed.append("source-mirror-markers")
        print("FAIL source-mirror-markers: a mirror whose source markers vanished passed")

    # A registered fence with no entry must fail too; otherwise deleting the
    # registration silently disables the mirror.
    with write_fixtures({
        "contracts/mirrors.json": json.dumps({"mirrors": [
            {"doc": "contracts/b.md", "fence": "text mirror", "source": "src/a.py", "begin": "# begin: a", "end": "# end: a"},
        ]}),
        "contracts/b.md": "# B\n\n```text mirror\nkeep = 2\n```\n",
        "contracts/a.md": "# A\n\n```text mirror\nkeep = 2\n```\n",
        "src/a.py": "# begin: a\nkeep = 2\n# end: a\n",
    }) as orphan_dir:
        orphan = RUNNERS["source-mirror"](Path(orphan_dir), {
            "id": "self-mirror-orphan",
            "kind": "source-mirror",
            "patterns": ["contracts/*.md"],
            "manifest": "contracts/mirrors.json",
        })
    if orphan:
        print(f"PASS source-mirror-orphan: an unregistered block was rejected ({orphan[0]})")
    else:
        failed.append("source-mirror-orphan")
        print("FAIL source-mirror-orphan: a block with no mirror entry passed")

    # A capability declared in source but never classified must fail; the registry
    # is hand-written, so without this direction an omission is invisible.
    with write_fixtures({
        **SELF_CAPABILITY_FILES,
        "src/extra.py": "# capability: unregistered\n",
    }) as capability_dir:
        unclassified = RUNNERS["capability-registry"](Path(capability_dir), SELF_CAPABILITY_CHECK)
    if unclassified:
        print(f"PASS capability-registry-discovery: an unclassified capability was rejected ({unclassified[0]})")
    else:
        failed.append("capability-registry-discovery")
        print("FAIL capability-registry-discovery: a marker with no registry entry passed")

    # A criterion whose id was dropped, whose owner does not exist, or whose section
    # carries no bullets must each fail: an untraceable criterion is the one failure
    # this kind exists to catch, and it has three distinct shapes.
    criteria_probes = (
        ("criteria-no-id", "- the feature works as intended.\n", "an untraced criterion"),
        ("criteria-unknown-owner", "- [A1] `no-such-check` rejects it.\n", "a criterion naming an undeclared owner"),
        ("criteria-empty-section", "", "a section with no criterion bullets"),
    )
    for probe, bullet, label in criteria_probes:
        with write_fixtures({
            **SELF_CRITERIA_FILES,
            ".agents/notes/implemented/TEMPLATE.md": f"# Decision\n\n## Testing\n\n{bullet}",
        }) as criteria_dir:
            untraced = RUNNERS["criteria-traced"](Path(criteria_dir), SELF_CRITERIA_CHECK)
        if untraced:
            print(f"PASS {probe}: {label} was rejected ({untraced[0]})")
        else:
            failed.append(probe)
            print(f"FAIL {probe}: {label} passed")

    # A budget must fail on whichever unit it declares: a word ceiling that never fires
    # on a file short in lines would be a green light over an unbounded page.
    word_check = {"id": "self-budget-words", "kind": "budget", "patterns": ["NOTE.md"], "maxWords": 10}
    with write_fixtures({"NOTE.md": " ".join(str(number) for number in range(40)) + "\n"}) as word_dir:
        over_words = RUNNERS["budget"](Path(word_dir), word_check)
    with write_fixtures({"NOTE.md": "one two three\n"}) as short_dir:
        under_words = RUNNERS["budget"](Path(short_dir), word_check)
    if over_words and not under_words:
        print(f"PASS budget-words: a file over its word ceiling was rejected ({over_words[0]})")
    else:
        failed.append("budget-words")
        print("FAIL budget-words: the word ceiling did not fire, or a file under it was rejected")

    # The publication manifest has three failure directions beyond the one the main case
    # covers: a public promise with no file behind it, one file caught by two classes,
    # and an internal entry that never says why. Each must fail on its own.
    publish_probes = (
        (
            "publish-stale-public",
            {"public": ["docs/a.md", "docs/gone.md"], "internal": []},
            {"docs/a.md": "# A\n"},
            "a public entry with no file behind it",
        ),
        (
            "publish-public-glob",
            {"public": ["docs/*.md"], "internal": []},
            {"docs/a.md": "# A\n"},
            "a public entry that publishes a whole directory",
        ),
        (
            "publish-ambiguous",
            {"public": ["docs/a.md"], "internal": [{"pattern": "docs/*.md", "reason": "caught by both sides"}]},
            {"docs/a.md": "# A\n"},
            "one file matched by both classes",
        ),
        (
            "publish-no-reason",
            {"public": ["docs/a.md"], "internal": [{"pattern": "docs/b.md"}]},
            {"docs/a.md": "# A\n", "docs/b.md": "# B\n"},
            "an internal entry with no reason",
        ),
    )
    for probe, manifest, files, label in publish_probes:
        with write_fixtures({**files, "docs/publish.json": json.dumps(manifest)}) as publish_dir:
            rejected_publish = RUNNERS["publish-manifest"](Path(publish_dir), {
                "id": f"self-{probe}",
                "kind": "publish-manifest",
                "patterns": ["docs/*.md"],
                "manifest": "docs/publish.json",
            })
        if rejected_publish:
            print(f"PASS {probe}: {label} was rejected ({rejected_publish[0]})")
        else:
            failed.append(probe)
            print(f"FAIL {probe}: {label} passed")

    # A seal has five failure directions, and one permitted empty state. Each direction
    # must fire on its own: a guard that only catches a drifted hash would let a file sit
    # in the archive unsealed, or let the manifest and the record disagree about the date.
    def seal_entry(path: str, text: str, archived: str = "2026-01-01") -> dict:
        return {
            "path": path,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "archived": archived,
            "reason": "covered by the successor record",
        }

    sealed_probes = (
        (
            "seal-unsealed-file",
            {"sealed": []},
            {"archive/b.md": SELF_SEALED_TEXT},
            "an archive file nobody sealed",
        ),
        (
            "seal-missing-file",
            {"sealed": [seal_entry("archive/gone.md", SELF_SEALED_TEXT)]},
            {},
            "a seal whose file no longer exists",
        ),
        (
            "seal-drifted-file",
            {"sealed": [seal_entry("archive/a.md", SELF_SEALED_TEXT)]},
            {"archive/a.md": SELF_SEALED_TEXT.replace("Old record", "Edited record")},
            "a sealed file edited after sealing",
        ),
        (
            "seal-header-mismatch",
            {"sealed": [seal_entry("archive/a.md", SELF_SEALED_TEXT, archived="2026-02-02")]},
            {"archive/a.md": SELF_SEALED_TEXT},
            "a seal whose date disagrees with the record it seals",
        ),
        (
            "seal-outside-patterns",
            {"sealed": [seal_entry(".agents/notes/x.md", SELF_SEALED_TEXT)]},
            {".agents/notes/x.md": SELF_SEALED_TEXT},
            "a seal outside the scanned corpus",
        ),
    )
    for probe, manifest, files, label in sealed_probes:
        with write_fixtures({**files, "archive/manifest.json": json.dumps(manifest)}) as sealed_dir:
            rejected_seal = RUNNERS["sealed-manifest"](Path(sealed_dir), SELF_SEALED_CHECK)
        if rejected_seal:
            print(f"PASS {probe}: {label} was rejected ({rejected_seal[0]})")
        else:
            failed.append(probe)
            print(f"FAIL {probe}: {label} passed")

    # The other direction: an archive is append-only, so it legitimately starts empty, and
    # an empty seal list must not be reported as a violation. The guard stays live because
    # a file dropped in without a seal is rejected by the probe above.
    with write_fixtures({"archive/manifest.json": json.dumps({"sealed": []})}) as empty_seal_dir:
        accepted_empty = RUNNERS["sealed-manifest"](Path(empty_seal_dir), SELF_SEALED_CHECK)
    if accepted_empty:
        failed.append("seal-empty-archive")
        print(f"FAIL seal-empty-archive: an archive with nothing sealed was rejected ({accepted_empty[0]})")
    else:
        print("PASS seal-empty-archive: an archive with nothing sealed is accepted, and the unsealed-file probe keeps the guard live")

    if failed:
        print(f"check-invariants: self-test FAILED for {', '.join(failed)}", file=sys.stderr)
        return 1
    print("check-invariants: self-test PASSED — every check rejects an invalid fixture and accepts a valid one, a check with no subject is rejected, and every registration direction is covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())


SELF_TEST_CASES = (tiers.SELF_TEST_CASES + policies.SELF_TEST_CASES + mirrors.SELF_TEST_CASES + capabilities.SELF_TEST_CASES
                   + records.SELF_TEST_CASES + publication.SELF_TEST_CASES + seals.SELF_TEST_CASES + criteria.SELF_TEST_CASES
                   + skills.SELF_TEST_CASES + links.SELF_TEST_CASES + evidence.SELF_TEST_CASES)
