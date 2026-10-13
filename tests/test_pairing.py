"""The pairing layer: a record per pair, recomputed rather than trusted, and the two sides the same shape."""

from __future__ import annotations


from tests.harness import ROOT, WORKFLOW, load_checker, load_pair_docs, load_run_evidence, sections
from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest


class StructureMirror(unittest.TestCase):
    """A counterpart that lost a row, an item, or a heading level is a different document."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_pair_docs()

    EN = "# A\n\nEnglish | [中文](a.zh.md)\n\n## S\n\n| x | y |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n- one\n- two\n\n```sh\nrun\n```\n"

    def test_a_table_that_lost_a_row_is_rejected(self):
        zh = self.EN.replace("| 3 | 4 |\n", "")
        self.assertTrue(self.module.structure_violations("a.md", self.EN, zh))

    def test_a_list_that_lost_an_item_is_rejected(self):
        zh = self.EN.replace("- two\n", "")
        self.assertTrue(self.module.structure_violations("a.md", self.EN, zh))

    def test_a_deeper_heading_is_rejected(self):
        zh = self.EN.replace("## S", "### S")
        self.assertTrue(self.module.structure_violations("a.md", self.EN, zh))

    def test_a_fence_whose_info_string_drifted_is_rejected(self):
        zh = self.EN.replace("```sh", "```bash")
        self.assertTrue(self.module.structure_violations("a.md", self.EN, zh))

    def test_the_same_shape_in_another_language_is_accepted(self):
        zh = "# A\n\n[English](a.md) | 中文\n\n## 小节\n\n| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n- 一\n- 二\n\n```sh\n运行\n```\n"
        self.assertEqual(self.module.structure_violations("a.md", self.EN, zh), [])


class PairDocs(unittest.TestCase):
    """The pairing layer: one record per pair, recomputed rather than trusted."""

    EN = "# A\n\nEnglish | [中文](a.zh.md)\n\n## S\n\ntext\n"
    ZH = "# A\n\n[English](a.md) | 中文\n\n## S\n\n译文\n"

    @classmethod
    def setUpClass(cls):
        cls.module = load_pair_docs()

    def build(self, en: str = "", zh: str = "", record: bool = True):
        """Write a throwaway one-pair kit and return (root, pairing, anchor set)."""
        root = Path(tempfile.mkdtemp())
        (root / "tools").mkdir()
        (root / "tools" / "workflow.json").write_text(
            json.dumps({
                "version": 1,
                "pairing": {"patterns": ["*.md"], "exclude": [], "hostCanonical": []},
                "surfaces": [],
                "checks": [],
            }),
            encoding="utf-8",
        )
        (root / "a.md").write_text(en or self.EN, encoding="utf-8")
        (root / "a.zh.md").write_text(zh or self.ZH, encoding="utf-8")
        pairing = self.module.load_pairing(root)
        anchors = {"a.md"}
        if record:
            current = self.module.compute_record("", "a.md", root, anchors, [])
            (root / "a.i18n.yaml").write_text(self.module.render_record("a.md", current, False, ""), encoding="utf-8")
        return root, pairing, anchors

    def violations(self, root: Path, pairing: dict, anchors: set) -> list[str]:
        return self.module.check_anchor(root, "", "a.md", anchors, pairing)

    def test_a_complete_pair_is_accepted(self):
        root, pairing, anchors = self.build()
        self.assertEqual(self.violations(root, pairing, anchors), [])

    def test_the_record_round_trips_through_its_canonical_text(self):
        root, _pairing, anchors = self.build()
        current = self.module.compute_record("", "a.md", root, anchors, [])
        rendered = self.module.render_record("a.md", current, False, "")
        self.assertEqual(self.module.parse_record(rendered), current)
        self.assertTrue(rendered.endswith("\n") and not rendered.endswith("\n\n"))

    def test_the_hash_model_is_pinned(self):
        """A fixed pair hashes to fixed section digests; changing the model breaks this."""
        root, _pairing, anchors = self.build()
        current = self.module.compute_record("", "a.md", root, anchors, [])
        self.assertEqual(current["/a"]["en"], "c201dd9bac9afc62")
        self.assertEqual(current["/a/s"]["zh"], "7f313732bbf1dea6")

    def test_a_missing_switcher_is_rejected(self):
        root, pairing, anchors = self.build(zh="# A\n\n## S\n\n译文\n")
        self.assertTrue(any("language switcher" in item for item in self.violations(root, pairing, anchors)))

    def test_a_one_sided_edit_is_rejected(self):
        root, pairing, anchors = self.build()
        (root / "a.zh.md").write_text(self.ZH.replace("译文", "改过的译文"), encoding="utf-8")
        self.assertTrue(any("out of sync" in item for item in self.violations(root, pairing, anchors)))

    def test_a_one_sided_change_is_accepted_after_re_recording(self):
        """The negative control's other half: re-recording turns the same edit green."""
        root, pairing, anchors = self.build()
        (root / "a.zh.md").write_text(self.ZH.replace("译文", "改过的译文"), encoding="utf-8")
        self.assertTrue(self.violations(root, pairing, anchors))
        current = self.module.compute_record("", "a.md", root, anchors, [])
        (root / "a.i18n.yaml").write_text(self.module.render_record("a.md", current, False, ""), encoding="utf-8")
        self.assertEqual(self.violations(root, pairing, anchors), [])

    def test_differing_fenced_blocks_are_rejected(self):
        root, pairing, anchors = self.build(en=self.EN + "\n```sh\necho hi\n```\n")
        self.assertTrue(any("fenced code blocks differ" in item for item in self.violations(root, pairing, anchors)))

    def test_a_relative_link_to_a_missing_file_is_rejected(self):
        root = Path(tempfile.mkdtemp())
        (root / "a.zh.md").write_text("# A\n\n[English](a.md) | 中文\n\n## S\n\n[gone](../nope/missing.md)\n", encoding="utf-8")
        violations = self.module.link_violations(root, "a.zh.md", (root / "a.zh.md").read_text(encoding="utf-8"))
        self.assertTrue(any("names no file" in item for item in violations), violations)

    def test_a_relative_link_to_an_existing_file_is_accepted(self):
        root = Path(tempfile.mkdtemp())
        (root / "sub").mkdir()
        (root / "sub" / "other.md").write_text("# Other\n\n## Target\n", encoding="utf-8")
        (root / "a.md").write_text("# A\n\nEnglish | [中文](a.zh.md)\n\n[s](sub/other.md#target)\n", encoding="utf-8")
        violations = self.module.link_violations(root, "a.md", (root / "a.md").read_text(encoding="utf-8"))
        self.assertEqual(violations, [])

    def test_a_link_to_the_other_side_is_rejected(self):
        root, pairing, anchors = self.build(en=self.EN + "\nSee [a](a.zh.md).\n")
        self.assertTrue(any("needs the English side" in item for item in self.violations(root, pairing, anchors)))

    def test_a_missing_record_is_rejected(self):
        root, pairing, anchors = self.build(record=False)
        self.assertTrue(any("incomplete pair" in item for item in self.violations(root, pairing, anchors)))

    def test_every_in_scope_document_has_a_complete_pair(self):
        """The kit's own corpus, read from the real tree rather than from a fixture."""
        pairing = self.module.load_pairing(ROOT)
        anchors = self.module.anchor_paths(ROOT, pairing)
        self.assertTrue(anchors, "the corpus is not empty")
        for anchor in anchors:
            with self.subTest(anchor=anchor):
                self.assertEqual(
                    self.module.check_anchor(ROOT, self.module.discover_prefix(ROOT), anchor, set(anchors), pairing),
                    [],
                )

    def test_travelling_records_carry_the_kit_s_own_command(self):
        """A record that moves with the kit must not name the host repository's path."""
        for anchor in WORKFLOW["pairing"]["hostCanonical"]:
            with self.subTest(anchor=anchor):
                record = (ROOT / anchor).with_suffix(".i18n.yaml").read_text(encoding="utf-8")
                self.assertIn(f"python3 tools/pair-docs.py --write {anchor}", record)
                self.assertNotIn("pnpm run verify-translation-pairing", record)

    def test_every_pair_lists_all_three_siblings(self):
        pairing = self.module.load_pairing(ROOT)
        for anchor in self.module.anchor_paths(ROOT, pairing):
            with self.subTest(anchor=anchor):
                stem = anchor[: -len(".md")]
                for sibling in (f"{stem}.zh.md", f"{stem}.i18n.yaml"):
                    self.assertTrue((ROOT / sibling).is_file(), f"{sibling} is missing")

    def test_a_fragment_naming_a_real_heading_is_accepted(self):
        root, pairing, anchors = self.build(
            en=self.EN + "\nSee [S](a.md#s).\n",
            zh=self.ZH + "\n见 [S](a.zh.md#s)。\n",
        )
        self.assertEqual(self.violations(root, pairing, anchors), [])

    def test_a_fragment_naming_no_heading_is_rejected(self):
        """The translated side names its own anchors, so a stale English fragment must fail."""
        root, pairing, anchors = self.build(zh=self.ZH + "\n见 [S](a.zh.md#no-such-section)。\n")
        self.assertTrue(any("names no heading" in item for item in self.violations(root, pairing, anchors)))

    def test_the_slug_rule_drops_punctuation_and_keeps_spaces_as_hyphens(self):
        self.assertEqual(self.module.github_slug("📄 · 文档（横切十一站）"), "--文档横切十一站")
        self.assertEqual(self.module.github_slug("Generated, mirrored, or linked"), "generated-mirrored-or-linked")

class SwitcherTargets(unittest.TestCase):
    """A switcher that names its own side is present but useless."""

    EN = "# Title\n\nEnglish | [中文](a.zh.md)\n\n## Section\n\nbody\n"
    ZH = "# 标题\n\n[English](a.md) | 中文\n\n## 小节\n\n正文\n"

    def check(self, en, zh):
        module = load_pair_docs()
        return module.switcher_target_violations("a", en, zh, "a")

    def test_the_convention_is_accepted(self):
        self.assertEqual(self.check(self.EN, self.ZH), [])

    def test_a_switcher_that_names_its_own_side_is_rejected(self):
        self.assertTrue(self.check(self.EN.replace("a.zh.md", "a.md"), self.ZH))

    def test_a_chinese_switcher_that_names_the_chinese_side_is_rejected(self):
        self.assertTrue(self.check(self.EN, self.ZH.replace("[English](a.md)", "[English](a.zh.md)")))


class ThePairingToolReadsTheRepository(unittest.TestCase):
    """A file git ignores is not in the repository, so it needs no exclusion here either."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_pair_docs()

    def test_an_ignored_document_is_not_an_anchor(self):
        """The pairing tool takes the same corpus the checks do, rather than walking the disk.

        Otherwise a project has to name every scratch directory in the pairing exclusions, one
        entry per directory, and that entry becomes load-bearing for a file nobody can commit.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            (root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
            (root / "kept.md").write_text("# kept\n", encoding="utf-8")
            (root / "scratch").mkdir()
            (root / "scratch" / "brief.md").write_text("# brief\n", encoding="utf-8")
            pairing = {"patterns": ["*.md"], "exclude": []}
            self.assertEqual(self.module.anchor_paths(root, pairing), ["kept.md"])


class ANamedPathKeepsItsOwnPrefix(unittest.TestCase):
    """A command-line anchor is a repository-relative path, so only a leading `./` is noise.

    A bare `lstrip("./")` also eats the dot that names a hidden directory, and every record
    the kit tells you to write lives under `.agents/`: the command its own documented
    `--write <path>` recipe prints is then rejected as "not an in-scope pair".
    """

    @classmethod
    def setUpClass(cls):
        cls.module = load_pair_docs()

    def test_the_documented_form_still_names_its_pair(self):
        self.assertEqual(self.module.select_anchors(["./a.md"], ["a.md"]), ["a.md"])

    def test_a_hidden_directory_keeps_its_dot(self):
        self.assertEqual(self.module.select_anchors([".agents/note.md"], [".agents/note.md"]), [".agents/note.md"])

    def test_no_anchor_means_every_pair(self):
        self.assertEqual(self.module.select_anchors([], ["a.md"]), ["a.md"])

    def test_an_unknown_anchor_is_refused(self):
        with self.assertRaises(SystemExit):
            self.module.select_anchors(["ghost.md"], ["a.md"])

    def test_the_recorded_name_is_the_one_the_cli_accepts(self):
        """The failure the fix removes, driven end to end through the real parser."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            (root / "tools" / "workflow.json").write_text(json.dumps({"pairing": {
                "patterns": ["*.md"], "exclude": [], "hostCanonical": [], "why": "fixture",
            }}), encoding="utf-8")
            (root / ".agents" / "notes").mkdir(parents=True)
            anchor = root / ".agents" / "notes" / "note.md"
            anchor.write_text("# A\n\nEnglish | [中文](note.zh.md)\n\n## S\n\nbody\n", encoding="utf-8")
            (root / ".agents" / "notes" / "note.zh.md").write_text(
                "# A\n\n[English](note.md) | 中文\n\n## S\n\n正文\n", encoding="utf-8")
            self.run_pair_docs(root, "--write", ".agents/notes/note.md")
            self.assertTrue((root / ".agents" / "notes" / "note.i18n.yaml").is_file())
            self.run_pair_docs(root, "--check", ".agents/notes/note.md")

    def run_pair_docs(self, root: Path, mode: str, anchor: str) -> None:
        argv = sys.argv
        sys.argv = ["pair-docs.py", mode, "--root", str(root), anchor]
        try:
            self.assertEqual(self.module.main(), 0)
        finally:
            sys.argv = argv
