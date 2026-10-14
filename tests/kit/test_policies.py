"""The policy owners: each standing document carries the shape its check gates, and the declarations agree with it."""

from __future__ import annotations


from tests.harness import ROOT, WORKFLOW, load_checker, load_run_evidence, sections
import unittest


class TestingPolicy(unittest.TestCase):
    """testing.md is the policy owner; its shape is what testing-policy gates."""

    REQUIRED = [
        "## Tiers",
        "## Evidence per change",
        "## Determinism",
        "## Blocking and observational lanes",
        "## Prove a new guard",
        "## Flake policy",
    ]

    def test_policy_carries_every_required_section(self):
        present = sections(ROOT / "docs" / "testing.md")
        for required in self.REQUIRED:
            with self.subTest(section=required):
                self.assertIn(required, present)

    def test_policy_names_the_kits_own_commands(self):
        text = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
        self.assertIn("tools/check-invariants.py", text)
        self.assertIn("tools/change-scope.py", text)


class DocsPolicy(unittest.TestCase):
    """documentation.md is the documentation standard; its shape is what docs-policy gates."""

    REQUIRED = [
        "## Document kinds",
        "## One home per fact",
        "## Tutorial or reference",
        "## Generated, mirrored, or linked",
        "## Budgets",
        "## Publication",
        "## The slop checklist",
    ]

    def test_policy_carries_every_required_section(self):
        present = sections(ROOT / "docs" / "documentation.md")
        for required in self.REQUIRED:
            with self.subTest(section=required):
                self.assertIn(required, present)

    def test_policy_names_the_generator_and_the_publication_switch(self):
        text = (ROOT / "docs" / "documentation.md").read_text(encoding="utf-8")
        self.assertIn("tools/gen-docs.py", text)
        self.assertIn("docs/publish.json", text)


class EvolutionPolicy(unittest.TestCase):
    """evolution.md owns the evolution and retirement rules; evolution-policy gates its shape."""

    REQUIRED = [
        "## Which record for which change",
        "## Write it in the change that breaks it",
        "## Version state",
        "## Consumed artifacts are append-only",
        "## Retire or archive",
        "## What this file does not own",
    ]

    def test_policy_carries_every_required_section(self):
        present = sections(ROOT / "docs" / "evolution.md")
        for required in self.REQUIRED:
            with self.subTest(section=required):
                self.assertIn(required, present)

    def test_policy_names_the_seal_and_its_registry(self):
        text = (ROOT / "docs" / "evolution.md").read_text(encoding="utf-8")
        self.assertIn(".agents/notes/archived/manifest.json", text)
        self.assertIn("archive-seal", text)


class WorkflowConfig(unittest.TestCase):
    """The config is the interface between the tools; a broken entry fails silently."""

    # The runner owns the set; a copy here would be a second fact that drifts. The
    # deliberate change-detector for a new kind is the registered mirror in contracts/kinds.md.
    KNOWN_KINDS = set(load_checker().SUPPORTED_KINDS)

    def test_every_check_declares_an_id_and_a_known_kind(self):
        seen = set()
        for check in WORKFLOW["checks"]:
            with self.subTest(check=check.get("id")):
                self.assertIn(check["kind"], self.KNOWN_KINDS)
                self.assertNotIn(check["id"], seen, "check ids must be unique")
                seen.add(check["id"])

    def test_every_surface_declares_a_reason_and_evidence(self):
        for surface in WORKFLOW["surfaces"]:
            with self.subTest(surface=surface["name"]):
                self.assertTrue(surface.get("why"), "a surface without a reason is noise")
                self.assertTrue(surface.get("evidence"), "a surface without evidence proves nothing")


class VocabularyLayer(unittest.TestCase):
    """Two homes for words: what a term means, and how to say it to a human."""

    TERM_DOCS = ("docs/glossary.md", "docs/glossary.zh.md", "docs/plain-language.md", "docs/plain-language.zh.md")

    def test_the_index_routes_both_vocabulary_homes(self):
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        self.assertIn("glossary.md", index)
        self.assertIn("plain-language.md", index)

    def test_the_standing_rules_link_both_vocabulary_homes(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("docs/glossary.md", agents)
        self.assertIn("docs/plain-language.md", agents)

    def test_term_headings_are_identifiers_on_both_sides(self):
        """A term heading stays ASCII, so one anchor works from either language."""
        for relative in self.TERM_DOCS:
            for line in (ROOT / relative).read_text(encoding="utf-8").split("\n"):
                if line.startswith("### "):
                    with self.subTest(doc=relative, heading=line):
                        self.assertTrue(line.isascii(), "a translated term heading would break cross-language links")
