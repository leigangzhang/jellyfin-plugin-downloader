"""The corpus guard: a check reads the repository, not every file on the disk."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import load_checker


class TestTheCorpusIsTheRepository(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_checker()

    def test_an_ignored_scratch_directory_is_not_a_subject(self):
        """A file git ignores is not in the repository, so no check may red on it.

        Without this the project has to declare every scratch directory in the publication
        switch and in the pairing exclusions, and a declaration it forgets turns a check red
        on a file that cannot ship.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            (root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
            (root / "kept.md").write_text("# kept\n", encoding="utf-8")
            (root / "scratch").mkdir()
            (root / "scratch" / "brief.md").write_text("# brief\n", encoding="utf-8")
            names = sorted(name for name, _ in self.module.iter_files(root, ["*.md"]))
            self.assertEqual(names, ["kept.md"])

    def test_iteration_selects_only_the_checks_that_read_the_change(self):
        """One edited page is read by a handful of checks, not by every check declared.

        This is the narrow mode a change runs while it is still moving. It is not the
        evidence a delivery needs: a guard's own test is a surface, so it is selected from
        the surface map rather than from any check's patterns.
        """
        config = {"checks": [
            {"id": "reads-the-page", "kind": "budget", "patterns": ["docs/*.md"]},
            {"id": "reads-the-gitignore", "kind": "budget", "patterns": [".gitignore"]},
            {"id": "reads-nothing-here", "kind": "budget", "patterns": ["src/*"], "exclude": ["src/generated/*"]},
        ]}
        selected = [check["id"] for check in self.module.select_checks(config, ["docs/guide.md"])]
        self.assertEqual(selected, ["reads-the-page"])
        self.assertEqual(self.module.select_checks(config, ["src/generated/x.py"]), [])

    def test_a_tree_without_git_is_walked_instead(self):
        """When git cannot answer, the walk is the fallback and it still finds the file."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kept.md").write_text("# kept\n", encoding="utf-8")
            names = sorted(name for name, _ in self.module.iter_files(root, ["*.md"]))
            self.assertEqual(names, ["kept.md"])


if __name__ == "__main__":
    unittest.main()
