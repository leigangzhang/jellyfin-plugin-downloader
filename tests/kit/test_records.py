"""The record layer: class folders, criteria, publication, seals, and the surfaces a change claims."""

from __future__ import annotations


from fnmatch import fnmatch
from tests.harness import ROOT, WORKFLOW, load_checker, load_run_evidence, sections, stage_installed
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest


class ArchiveSeal(unittest.TestCase):
    """A second, independent oracle for the seal the archive-seal check enforces.

    The check runs inside check-invariants.py; this reads the real registry and the real
    archive, so a registry that the check would pass for the wrong reason still has to
    account for every file, and every digest still has to match the bytes on disk.
    """

    @unittest.skipUnless(stage_installed("retirement"), "the retirement stage is not installed at this tier")
    def test_every_archived_file_is_sealed_and_unchanged(self):
        manifest = json.loads((ROOT / ".agents" / "notes" / "archived" / "manifest.json").read_text(encoding="utf-8"))
        sealed = {entry["path"]: entry for entry in manifest["sealed"]}
        corpus = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / ".agents" / "notes" / "archived").rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        self.assertEqual(corpus, set(sealed), "every archived file is registered exactly once")
        for relative, entry in sealed.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), entry["sha256"])
                self.assertIn(f"Archived: {entry['archived']}", text)


class PublicationManifest(unittest.TestCase):
    """A second, independent oracle for the rule publish-manifest enforces.

    The check runs inside check-invariants.py; this reads the real manifest and the real
    corpus, so a manifest that the check would pass for the wrong reason still has to
    account for every Markdown file in the repository.
    """

    @classmethod
    def setUpClass(cls):
        manifest = json.loads((ROOT / "docs" / "publish.json").read_text(encoding="utf-8"))
        cls.public = manifest["public"]
        cls.internal = manifest["internal"]

    @staticmethod
    def markdown_files() -> list[str]:
        """The corpus the publication switch reads, taken from its one source.

        Enumerating the disk here would disagree with the check the moment a project keeps
        a Markdown file git ignores: the check would skip it and this test would demand a
        classification for a file nobody can commit.
        """
        return sorted(name for name, _ in load_checker().iter_files(ROOT, ["*.md"]))

    def declared(self) -> list[tuple[str, str]]:
        return [(pattern, "public") for pattern in self.public] + [
            (entry["pattern"], "internal") for entry in self.internal
        ]

    def test_every_markdown_file_is_classified_exactly_once(self):
        for relative in self.markdown_files():
            with self.subTest(path=relative):
                hits = [pattern for pattern, _ in self.declared() if fnmatch(relative, pattern)]
                self.assertEqual(len(hits), 1, f"{relative} must match exactly one pattern, matched {hits}")

    def test_every_public_pattern_matches_a_real_file(self):
        files = self.markdown_files()
        for pattern in self.public:
            with self.subTest(pattern=pattern):
                self.assertTrue(
                    any(fnmatch(relative, pattern) for relative in files),
                    f"{pattern} promises a file that does not exist",
                )

    def test_public_entries_name_one_file_and_never_a_glob(self):
        for entry in self.public:
            with self.subTest(entry=entry):
                self.assertFalse(
                    any(magic in entry for magic in "*?["),
                    "publication is a per-file promise; a glob would publish files nobody chose",
                )

    def test_every_internal_entry_says_why_the_file_stays(self):
        for entry in self.internal:
            with self.subTest(pattern=entry.get("pattern")):
                self.assertTrue(entry.get("reason", "").strip(), "an internal entry must state its reason")


class NoteClasses(unittest.TestCase):
    """The class axis: a record's folder, its Class line, and the closed set must agree."""

    CHECK = {
        "id": "test-note-class",
        "kind": "note-class",
        "patterns": [".agents/notes/*/*/*.md"],
        "classes": ["feature", "bug-fix", "simplification", "architecture", "process", "testing"],
    }

    def run_check(self, files: dict[str, str]) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, text in files.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            return load_checker().check_note_class(root, self.CHECK)

    def test_a_record_filed_under_its_own_class_is_accepted(self):
        self.assertEqual(self.run_check({".agents/notes/implemented/process/x.md": "Class: process\n"}), [])

    def test_a_class_line_that_disagrees_with_its_folder_is_rejected(self):
        violations = self.run_check({".agents/notes/implemented/process/x.md": "Class: feature\n"})
        self.assertTrue(violations, "a misfiled record must be rejected")
        self.assertIn("disagrees", violations[0])

    def test_a_folder_outside_the_closed_set_is_rejected(self):
        violations = self.run_check({".agents/notes/implemented/docs/x.md": "Class: docs\n"})
        self.assertTrue(violations, "a class outside the closed set must be rejected")
        self.assertIn("unknown class", violations[0])

    def test_a_record_with_no_class_line_is_rejected(self):
        violations = self.run_check({".agents/notes/implemented/process/x.md": "Status: implemented\n"})
        self.assertTrue(violations, "an unconfirmed path class must be rejected")
        self.assertIn("no 'Class:' line", violations[0])


class Records(unittest.TestCase):
    """A few invariants the static checks cannot express about the kit's own records."""

    def test_notes_templates_trace_their_criteria(self):
        for name in ("proposed", "implemented"):
            with self.subTest(template=name):
                text = (ROOT / ".agents" / "notes" / name / "TEMPLATE.md").read_text(encoding="utf-8")
                self.assertRegex(text, r"\[A\d+\]", "every criterion in a template carries an id")

    def test_every_skill_carries_its_four_sections(self):
        for skill in sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md")):
            with self.subTest(skill=skill.parent.name):
                present = sections(skill)
                for required in ("## When to use", "## How", "## Verification", "## Anti-patterns"):
                    self.assertIn(required, present)

    @unittest.skipUnless(stage_installed("incident"), "the incident stage is not installed at this tier")
    def test_postmortem_template_traces_its_guardrails(self):
        """Every guardrail names the declared check or surface that now fails for it."""
        text = (ROOT / "dev" / "postmortem" / "TEMPLATE.md").read_text(encoding="utf-8")
        section = text.split("## Guardrail added", 1)[1].split("\n## ", 1)[0]
        owners = {entry["id"] for entry in WORKFLOW["checks"]} | {entry["name"] for entry in WORKFLOW["surfaces"]}
        bullets = [line for line in section.splitlines() if line.strip().startswith("- ")]
        self.assertTrue(bullets, "the template carries a guardrail bullet to trace")
        for bullet in bullets:
            with self.subTest(bullet=bullet[:60]):
                self.assertRegex(bullet, r"\[G\d+\]")
                self.assertTrue(owners.intersection(re.findall(r"`([a-z][a-z0-9-]*)`", bullet)))


class SurfaceExclude(unittest.TestCase):
    """A surface that excludes a path must not claim it, and must still claim the rest."""

    SURFACES = [
        {"name": "prose", "patterns": ["*.md"], "exclude": ["evidence/*"]},
        {"name": "i18n", "patterns": ["*.md", "*.i18n.yaml"], "exclude": ["evidence/*"]},
        {"name": "plain", "patterns": ["*.md"]},
    ]

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("change_scope", ROOT / "tools" / "change-scope.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("tools/change-scope.py is missing")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def claimed(self, path: str) -> list[str]:
        return [surface["name"] for surface in self.module.surfaces_for(path, self.SURFACES)]

    def test_an_excluded_path_is_not_claimed_by_that_surface(self):
        self.assertNotIn("prose", self.claimed("evidence/2026-10-07-a.md"))
        self.assertNotIn("i18n", self.claimed("evidence/2026-10-07-a.md"))

    def test_a_surface_without_exclude_still_claims_the_path(self):
        self.assertIn("plain", self.claimed("evidence/2026-10-07-a.md"))

    def test_an_ordinary_document_is_still_claimed(self):
        self.assertIn("prose", self.claimed("docs/documentation.md"))
        self.assertIn("i18n", self.claimed(".agents/notes/README.i18n.yaml"))
