import os
import subprocess
import sys

from temporary_loop import SCRIPTS, TemporaryLoopTest


class CheckPullRequestTest(TemporaryLoopTest):
    def check(self, title: str) -> tuple[int, str]:
        """Runs scripts/check-pull-request on the title, and gives its exit status and what it printed."""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "check-pull-request")],
            check=False,
            env=os.environ | {"TITLE": title},
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    def test_prints_each_problem_as_an_error_for_github_actions_and_fails(self):
        status, output = self.check("Skip duplicate expenses")
        self.assertEqual(status, 1)
        self.assertTrue(output.startswith('::error::The title "Skip duplicate expenses" doesn\'t follow'), output)

    def test_passes_a_title_without_problems(self):
        self.assertEqual(self.check("feat: skip expenses that were already imported"), (0, ""))
