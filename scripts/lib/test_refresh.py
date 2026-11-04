import json

import refreshing
import usage
from temporary_loop import TemporaryLoopTest


class RefreshTest(TemporaryLoopTest):
    def setUp(self):
        super().setUp()
        self.stand_in_gh('case "$1" in repo) echo owner/repo ;; api) echo open ;; esac')
        refreshing.STATE.write_text("# Goal\nShip the importer, waiting on #12.\n")
        usage.ORCHESTRATOR.write_text(json.dumps({"transcript": "orchestrator.jsonl"}))

    def test_shows_the_checklist(self):
        code, output = self.run_script("refresh")
        self.assertEqual(code, 0)
        self.assertIn(refreshing.CHECKLIST, output)
        self.assertTrue(refreshing.CHECKED.exists())
        self.assertFalse(refreshing.REQUEST.exists())

    def test_ready_before_the_checklist_was_shown_shows_it_and_requests_nothing(self):
        code, output = self.run_script("refresh", "--ready")
        self.assertEqual(code, 0)
        self.assertIn(refreshing.CHECKLIST, output)
        self.assertFalse(refreshing.REQUEST.exists())

    def test_ready_after_the_checklist_requests_the_refresh(self):
        refreshing.CHECKED.write_text("")
        code, output = self.run_script("refresh", "--ready")
        self.assertEqual(code, 0)
        self.assertIn("The refresh happens once this turn ends.", output)
        self.assertEqual((refreshing.load_request() or {}).get("transcript"), "orchestrator.jsonl")

    def test_ready_after_a_failed_refresh_is_refused(self):
        refreshing.CHECKED.write_text("")
        refreshing.FAILED.write_text("")
        code, output = self.run_script("refresh", "--ready")
        self.assertEqual(code, 1)
        self.assertIn("The last refresh failed", output)
        self.assertFalse(refreshing.REQUEST.exists())


class RefreshWhileGitHubFailsTest(TemporaryLoopTest):
    def setUp(self):
        super().setUp()
        self.stand_in_gh('echo "error connecting to api.github.com" >&2\nexit 1')

    def test_a_state_that_names_no_issue_can_be_refreshed(self):
        refreshing.STATE.write_text("# Goal\nShip the importer.\n")
        self.assertEqual(self.run_script("refresh")[0], 0)
        self.assertEqual(self.run_script("refresh", "--ready")[0], 0)
        self.assertTrue(refreshing.REQUEST.exists())

    def test_names_the_issues_it_couldnt_check_and_can_be_refreshed(self):
        refreshing.STATE.write_text("# Goal\nShip the importer, waiting on #12 and #15.\n")
        code, output = self.run_script("refresh")
        self.assertEqual(code, 0)
        self.assertIn("- #12: error connecting to api.github.com\n- #15: error connecting to api.github.com", output)
        self.assertEqual(self.run_script("refresh", "--ready")[0], 0)
        self.assertTrue(refreshing.REQUEST.exists())
