"""The tier switch: the switch, the tree, and the map's tier table must agree in both directions."""

from __future__ import annotations


from tests.harness import ROOT, WORKFLOW, load_checker, load_pair_docs, load_run_evidence, sections
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest


class TierSwitch(unittest.TestCase):
    """The switch, the tree, and the map's tier table must agree in both directions."""

    SWITCH = {
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
    TABLE = "| Tier | Stations |\n|---|---|\n| **Minimum** | 2 |\n| **Long-lived** | Everything + 4 |\n"
    FILES = {
        "tools/tiers.json": json.dumps(SWITCH),
        "dev/README.md": TABLE,
        ".agents/notes/proposed/2026-01-01-a.md": "# A\n",
        "dev/contracts/2026-01-01-b.md": "# B\n",
    }

    @classmethod
    def setUpClass(cls):
        cls.module = load_checker()
        cls.check = {"id": "tier-manifest", "kind": "tier-manifest", "patterns": ["tools/tiers.json"], "why": "fixture"}

    def violations(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for rel, text in files.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            return self.module.check_tier_manifest(root, self.check)

    def test_a_stage_declared_none_with_files_present_is_rejected(self):
        files = dict(self.FILES)
        files["tools/tiers.json"] = json.dumps({**self.SWITCH, "lifecycles": {"fixture": {"default": "minimum", "stages": {"contract": "none"}}}})
        self.assertTrue(self.violations(files))

    def test_an_installed_stage_with_no_files_is_rejected(self):
        files = {k: v for k, v in self.FILES.items() if k != "dev/contracts/2026-01-01-b.md"}
        self.assertTrue(self.violations(files))

    def test_a_tier_table_that_disagrees_is_rejected(self):
        files = dict(self.FILES)
        files["dev/README.md"] = self.TABLE.replace("Everything + 4", "Everything + 9")
        self.assertTrue(self.violations(files))

    def test_agreement_in_both_directions_is_accepted(self):
        self.assertEqual(self.violations(self.FILES), [])

    def test_a_stage_that_owns_several_paths_is_checked_at_each_one(self):
        """`home` may be a list: an absent stage must own none of the paths it lists."""
        switch = json.loads(json.dumps(self.SWITCH))
        switch["stages"]["proposal"]["home"] = [".agents/notes/proposed/*.md", "test_*.py"]
        files = dict(self.FILES)
        files["tools/tiers.json"] = json.dumps(switch)
        files["test_leftover.py"] = "# leftover\n"
        files["tools/tiers.json"] = json.dumps({**switch, "lifecycles": {"fixture": {"default": "long-lived", "stages": {"proposal": "none"}}}})
        self.assertTrue(self.violations(files))
