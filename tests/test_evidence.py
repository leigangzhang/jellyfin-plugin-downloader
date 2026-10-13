"""The evidence runner: it must never mistake a sentence or a placeholder for a command."""

from __future__ import annotations


from tests.harness import ROOT, WORKFLOW, load_checker, load_run_evidence, sections
from pathlib import Path
import subprocess
import tempfile
import unittest


class RunEvidence(unittest.TestCase):
    """The runner must never mistake a sentence or a placeholder for a command."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_run_evidence()

    def test_recognises_runnable_commands(self):
        for entry in ("python3 tools/check-invariants.py", "./scripts/check.sh", "make check"):
            with self.subTest(entry=entry):
                self.assertEqual(self.module.classify(entry)[0], "runnable")

    def test_treats_prose_as_manual_evidence(self):
        for entry in ("read the Alternatives considered section yourself", "run the owning test for each changed file"):
            with self.subTest(entry=entry):
                self.assertNotEqual(self.module.classify(entry)[0], "runnable")

    def test_a_command_shaped_entry_must_use_a_runner_token(self):
        for entry in ("definitely-not-on-path --check", "./scripts/missing.sh"):
            with self.subTest(entry=entry):
                self.assertTrue(self.module.looks_like_command(entry))

    def test_plain_prose_is_not_mistaken_for_a_command(self):
        for entry in ("read the Migration steps yourself", "confirm the outside install ran in a clean directory"):
            with self.subTest(entry=entry):
                self.assertFalse(self.module.looks_like_command(entry))

    def test_placeholders_are_never_runnable(self):
        self.assertEqual(self.module.classify("<your unit command>")[0], "placeholder")

    def test_a_placeholder_anywhere_disqualifies_the_entry(self):
        self.assertEqual(self.module.classify("python3 tools/x.py --base <ref>")[0], "placeholder")

    def test_a_command_that_cannot_run_is_unknown_not_failed(self):
        """A tooling problem is not a verdict about the change, and it is never a pass."""
        self.assertEqual(self.module.verdict_for(None, "could not spawn it")[0], "UNKNOWN")
        for code in (126, 127):
            with self.subTest(exit=code):
                self.assertEqual(self.module.verdict_for(subprocess.CompletedProcess(args="x", returncode=code), None)[0], "UNKNOWN")

    def test_a_command_that_ran_gets_a_pass_or_fail_verdict(self):
        for code, expected in ((0, "PASS"), (1, "FAIL"), (2, "FAIL")):
            with self.subTest(exit=code):
                self.assertEqual(self.module.verdict_for(subprocess.CompletedProcess(args="x", returncode=code), None)[0], expected)

    def test_resolution_is_checked_against_the_repository_root(self):
        self.assertTrue(self.module.resolves(ROOT, "python3 tools/check-invariants.py"))
        self.assertFalse(self.module.resolves(ROOT, "./tools/does-not-exist.py"))

    def test_the_plan_honours_a_surface_exclude(self):
        """`change-scope.py` honours an exclude; the runner must read the same map the same way."""
        plan, unmatched = self.module.build_plan(WORKFLOW, ["dev/evidence/2026-01-01-x.md"])
        self.assertEqual(plan, [])
        self.assertEqual(unmatched, [])

    def test_the_record_home_is_not_a_change_to_verify(self):
        """Counting the record would make a delivery name itself differently on every run."""
        plan, _ = self.module.build_plan(WORKFLOW, ["dev/evidence/a-abc12345.md", "tools/run-evidence.py"])
        self.assertEqual([surface["name"] for surface in plan], ["workflow", "source"])

    def test_an_empty_change_set_is_not_a_delivery(self):
        """A committed delivery leaves no change set; it must not leave a `worktree-<sha>` file."""
        self.assertTrue(self.module.nothing_to_verify([], None))
        self.assertFalse(self.module.nothing_to_verify([], Path("dev/evidence/asked-for.md")))

    def test_every_declared_command_resolves(self):
        violations = self.module.validate_commands(self.module.plan_for_all(WORKFLOW))
        self.assertEqual(violations, [], "a declared command that cannot run is not evidence")

    def test_one_delivery_keeps_one_record(self):
        """The name follows the delivery, not the run.

        A delivery is a (merge base, covered surfaces) pair. Running it again with the
        same verdict must find the first record instead of stacking a file beside it; the
        rule this replaces counted runs, so five runs left five files.
        """
        with tempfile.TemporaryDirectory() as directory:
            target = self.module.default_record_path(Path(directory), "source-tests", "abcdef1234567890")
            self.assertEqual(target.name, "source-tests-abcdef12.md")
            self.assertIsNone(self.module.recorded_verdict(target))
            self.assertEqual(self.module.record_target_for(target, "PASS"), target)
            target.parent.mkdir(parents=True)
            target.write_text("# Evidence record\n\nVerdict: PASS — 2 passed, 0 failed\n", encoding="utf-8")
            self.assertEqual(self.module.recorded_verdict(target), "PASS")
            self.assertIsNone(self.module.record_target_for(target, "PASS"))

    def test_a_changed_verdict_takes_a_successor(self):
        """A new claim about a new run may not be written into the old record."""
        with tempfile.TemporaryDirectory() as directory:
            target = self.module.default_record_path(Path(directory), "source", "abcdef1234567890")
            target.parent.mkdir(parents=True)
            target.write_text("# Evidence record\n\nVerdict: FAIL — 1 passed, 1 failed\n", encoding="utf-8")
            successor = self.module.record_target_for(target, "PASS")
            self.assertEqual(successor.name, "source-abcdef12-2.md")
            successor.write_text("# Evidence record\n\nVerdict: PASS — 2 passed, 0 failed\n", encoding="utf-8")
            self.assertEqual(self.module.recorded_verdict(target), "FAIL")
            # the newest record speaks for the delivery, so a repeat of it writes nothing
            self.assertIsNone(self.module.record_target_for(target, "PASS"))
            # and a verdict that changes again counts from the base name, not from -2
            third = self.module.record_target_for(target, "FAIL")
            self.assertEqual(third.name, "source-abcdef12-3.md")

    def test_a_worktree_only_run_is_named_for_the_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                self.module.default_record_path(Path(directory), "source", None).name,
                "source-worktree.md",
            )

    def test_a_deletion_does_not_hand_a_delivery_its_old_record_back(self):
        """A verdict with no record left is a new claim, so it writes the base name again."""
        with tempfile.TemporaryDirectory() as directory:
            target = self.module.default_record_path(Path(directory), "source", "abcdef1234567890")
            target.parent.mkdir(parents=True)
            target.write_text("# Evidence record\n\nVerdict: PASS — 1 passed, 0 failed\n", encoding="utf-8")
            target.unlink()
            self.assertIsNone(self.module.recorded_verdict(target))
            self.assertEqual(self.module.record_target_for(target, "PASS"), target)

    def test_a_record_is_named_for_what_it_covered(self):
        """On a long-lived branch the branch name says nothing, so the surfaces name it.

        Every record on `main` would otherwise be `<date>-main`, told apart by a counter: five
        such files, indistinguishable by name, sat in the adopting repository's evidence
        directory. A working branch is named for its work and is kept as it is.
        """
        self.assertEqual(self.module.record_slug("main", ["source", "tests", "i18n"]), "source-tests-i18n")
        self.assertEqual(self.module.record_slug("main", ["a", "b", "c", "d"]), "a-b-c-and-more")
        self.assertEqual(self.module.record_slug("main", []), "worktree")
        self.assertEqual(self.module.record_slug("feature/gomoku-ai", ["source"]), "feature-gomoku-ai")
