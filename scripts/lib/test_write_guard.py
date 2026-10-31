import io
import json
import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

# The hook is a script, so the tests load its functions rather than start a process for each case.
GUARD = runpy.run_path(str(Path(__file__).resolve().parents[1] / "hooks" / "write-guard"))
refusal = GUARD["refusal"]


class WriteGuardTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.worktree = self.folder / "builder-1"
        self.worktree.mkdir()
        (self.worktree / ".git").write_text("gitdir: elsewhere\n")
        self.outside = self.folder / "outside.py"
        self.outside.write_text("")

    def test_a_file_in_the_worktree_may_be_written(self):
        self.assertEqual(refusal(str(self.worktree / "scripts" / "new.py"), str(self.worktree)), "")

    def test_a_file_outside_the_worktree_is_refused(self):
        self.assertIn("is outside your worktree", refusal(str(self.outside), str(self.worktree)))

    def test_a_folder_whose_name_starts_with_the_worktrees_is_outside_it(self):
        path = self.folder / "builder-10" / "x.py"
        self.assertIn("is outside your worktree", refusal(str(path), str(self.worktree)))

    def test_a_path_that_leaves_the_worktree_through_dot_dot_is_refused(self):
        path = f"{self.worktree}/../outside.py"
        self.assertIn("is outside your worktree", refusal(path, str(self.worktree)))

    def test_a_link_in_the_worktree_to_a_file_outside_is_refused(self):
        os.symlink(self.outside, self.worktree / "link.py")
        self.assertIn("is outside your worktree", refusal(str(self.worktree / "link.py"), str(self.worktree)))

    def test_gits_own_files_are_refused_in_any_case_and_any_folder(self):
        for path in (".git", ".GIT", "vendor/repo/.git/config", "vendor/repo/.Git/hooks/pre-commit"):
            with self.subTest(path=path):
                self.assertIn("one of git's own files", refusal(str(self.worktree / path), str(self.worktree)))

    def test_a_relative_path_is_refused(self):
        self.assertIn("isn't an absolute path", refusal("scripts/new.py", str(self.worktree)))

    def test_a_refused_notebook_edit_stops_the_tool_with_the_reason(self):
        event = {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(self.outside)}}
        errors = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(json.dumps(event))),
            mock.patch("sys.argv", ["write-guard", str(self.worktree)]),
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as raised,
        ):
            GUARD["main"]()
        # Claude Code stops a tool call when a PreToolUse hook exits with 2, and shows the agent what it printed.
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("is outside your worktree", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
