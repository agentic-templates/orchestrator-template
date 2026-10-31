import io
import json
import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

# The hook is a script, so the tests load it rather than start a process for each case.
HOOK = Path(__file__).resolve().parents[1] / "hooks" / "write-guard"
refusal = runpy.run_path(str(HOOK))["refusal"]


class WriteGuardTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.worktree = self.folder / "builder-1"
        self.worktree.mkdir()
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
                self.assertIn("is a .git file or in a .git folder", refusal(str(self.worktree / path), str(self.worktree)))

    def test_a_relative_path_is_refused(self):
        self.assertIn("isn't an absolute path", refusal("scripts/new.py", str(self.worktree)))

    def test_a_call_without_a_path_is_refused(self):
        self.assertIn("names no file", refusal("", str(self.worktree)))

    def run_hook(self, stdin: str) -> tuple[int | str | None, str]:
        """The hook's exit code, None if it lets the call through, and what it printed for the agent."""
        errors = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            mock.patch("sys.argv", ["write-guard", str(self.worktree)]),
            redirect_stderr(errors),
        ):
            try:
                runpy.run_path(str(HOOK), run_name="__main__")
            except SystemExit as exit:
                return exit.code, errors.getvalue()
        return None, errors.getvalue()

    def test_a_write_in_the_worktree_goes_through(self):
        event = {"tool_name": "Write", "tool_input": {"file_path": str(self.worktree / "new.py")}}
        self.assertEqual(self.run_hook(json.dumps(event)), (None, ""))

    def test_a_refused_notebook_edit_stops_the_tool_with_the_reason(self):
        event = {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(self.outside)}}
        code, errors = self.run_hook(json.dumps(event))
        # Claude Code stops a tool call when a PreToolUse hook exits with 2, and shows the agent what it printed.
        self.assertEqual(code, 2)
        self.assertIn("is outside your worktree", errors)

    def test_input_the_hook_cant_read_stops_the_tool(self):
        code, errors = self.run_hook("not json")
        self.assertEqual(code, 2)
        self.assertIn("The write guard failed", errors)


if __name__ == "__main__":
    unittest.main()
