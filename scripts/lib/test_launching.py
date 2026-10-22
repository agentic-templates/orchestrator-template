import json
import unittest
from pathlib import Path

import launching
import meter
import usage

ACCOUNTS = [
    usage.Account("claude", "claude", None),
    usage.Account("claude-2", "claude", "~/.claude-2"),
    usage.Account("codex", "codex", None),
]


def decisions(claude_room: int, claude_2_room: int, codex_room: int = 5) -> dict[str, meter.Decision]:
    def decision(room: int) -> meter.Decision:
        return meter.Decision("start" if room else "hold", room, 0, 40.0, 0.1)

    return {"claude": decision(claude_room), "claude-2": decision(claude_2_room), "codex": decision(codex_room)}


class ChooseAccountTest(unittest.TestCase):
    def test_a_requested_account_with_room_is_used(self):
        account, _ = launching.choose_account(decisions(1, 3), ACCOUNTS, "claude", force=False)
        self.assertEqual(account.name, "claude")

    def test_a_requested_account_without_room_is_refused_with_the_meter_report(self):
        account, reason = launching.choose_account(decisions(0, 3), ACCOUNTS, "claude", force=False)
        self.assertIsNone(account)
        self.assertIn("without --account", reason)
        self.assertIn("Start the next agent on: claude-2", reason)

    def test_force_starts_on_a_requested_account_without_room(self):
        account, _ = launching.choose_account(decisions(0, 0, 0), ACCOUNTS, "claude", force=True)
        self.assertEqual(account.name, "claude")

    def test_without_a_request_the_account_with_room_is_used(self):
        account, _ = launching.choose_account(decisions(0, 2), ACCOUNTS, None, force=False)
        self.assertEqual(account.name, "claude-2")

    def test_without_a_request_an_account_the_launcher_cant_start_isnt_used(self):
        account, _ = launching.choose_account(decisions(0, 0, codex_room=5), ACCOUNTS, None, force=False)
        self.assertIsNone(account)

    def test_without_room_anywhere_nothing_starts(self):
        account, reason = launching.choose_account(decisions(0, 0, 0), ACCOUNTS, None, force=False)
        self.assertIsNone(account)
        self.assertIn("No account has room", reason)

    def test_an_account_that_the_launcher_cant_start_is_refused(self):
        account, reason = launching.choose_account(decisions(1, 3), ACCOUNTS, "codex", force=True)
        self.assertIsNone(account)
        self.assertIn("can't start agents there", reason)

    def test_an_unknown_account_is_refused(self):
        account, reason = launching.choose_account(decisions(1, 1), ACCOUNTS, "claude-9", force=False)
        self.assertIsNone(account)
        self.assertIn("No account is called claude-9", reason)


class SettingsTest(unittest.TestCase):
    def test_the_sandbox_lets_an_agent_write_only_its_own_uv_cache(self):
        writable = json.loads(launching.settings())["sandbox"]["filesystem"]["allowWrite"]
        self.assertEqual(writable, [str(launching.UV_CACHE)])

    def test_an_agent_cant_run_a_command_outside_the_sandbox(self):
        self.assertIs(json.loads(launching.settings())["sandbox"]["allowUnsandboxedCommands"], False)


class HoldingTest(unittest.TestCase):
    PORCELAIN = (
        "worktree /repo\nHEAD 1\nbranch refs/heads/main\n\n"
        "worktree /repo/.loop/worktrees/builder-42-a\nHEAD 2\nbranch refs/heads/issue-42\n\n"
        "worktree /repo/.loop/worktrees/builder-4-b\nHEAD 3\nbranch refs/heads/issue-4\n\n"
        "worktree /repo/.loop/worktrees/reviewer-9-c\nHEAD 4\ndetached"
    )

    def test_only_the_worktree_with_exactly_that_branch(self):
        self.assertEqual(launching.holding(self.PORCELAIN, "issue-4"), [Path("/repo/.loop/worktrees/builder-4-b")])

    def test_a_branch_nobody_holds(self):
        self.assertEqual(launching.holding(self.PORCELAIN, "issue-5"), [])


class BuiltTest(unittest.TestCase):
    def test_the_record_says_whether_the_agent_builds(self):
        self.assertFalse(launching.built({"name": "reviewer", "builds": False}))

    def test_a_record_without_the_answer_counts_as_a_builders(self):
        self.assertTrue(launching.built({"name": "builder-old"}))


class BusyWorktreeTest(unittest.TestCase):
    def test_a_stopped_agents_worktree_can_be_freed(self):
        self.assertEqual(launching.busy_worktree(launching.WORKTREES / "builder-7-abc", "issue-7", running=[]), "")

    def test_a_running_agents_worktree_is_refused(self):
        path = launching.WORKTREES / "builder-7-abc"
        reason = launching.busy_worktree(path, "issue-7", running=[{"worktree": str(path)}])
        self.assertIn("still works in", reason)

    def test_a_worktree_outside_the_loops_folder_is_refused(self):
        reason = launching.busy_worktree(Path("/work/my-checkout"), "issue-7", running=[])
        self.assertIn("isn't one of the loop's worktrees", reason)


class PushAdviceTest(unittest.TestCase):
    REJECTED = (
        "To github.com:owner/repo.git\n ! [rejected]        issue-4 -> issue-4 (non-fast-forward)\n"
        "error: failed to push some refs to 'github.com:owner/repo.git'\n"
        "hint: Updates were rejected because the tip of your current branch is behind\n"
        "hint: its remote counterpart. If you want to integrate the remote changes,\n"
        "hint: use 'git pull' before pushing again.\n"
    )
    OFFLINE = "fatal: unable to access 'https://github.com/owner/repo.git/': Could not resolve host: github.com\n"

    def test_git_hints_are_left_out(self):
        error = launching.push_error(self.REJECTED)
        self.assertIn("[rejected]", error)
        self.assertNotIn("git pull", error)

    def test_a_refused_push_goes_to_the_maintainer(self):
        advice = launching.push_advice(launching.push_error(self.REJECTED), Path("/w"))
        self.assertIn("Tell the maintainer", advice)

    def test_a_push_a_ruleset_declines_goes_to_the_maintainer(self):
        declined = (
            "remote: error: GH013: Repository rule violations found for refs/heads/issue-4.\n"
            " ! [remote rejected] issue-4 -> issue-4 (push declined due to repository rule violations)\n"
        )
        self.assertIn("Tell the maintainer", launching.push_advice(launching.push_error(declined), Path("/w")))

    def test_a_network_failure_is_pushed_again(self):
        advice = launching.push_advice(launching.push_error(self.OFFLINE), Path("/w"))
        self.assertIn("pushes it again", advice)
        self.assertNotIn("maintainer", advice)

    def test_the_cause_of_an_ssh_failure_is_kept(self):
        offline = (
            "ssh: Could not resolve hostname github.com: nodename nor servname provided\n"
            "fatal: Could not read from remote repository.\n\n"
            "Please make sure you have the correct access rights\nand the repository exists.\n"
        )
        error = launching.push_error(offline)
        self.assertIn("Could not resolve hostname", error)
        self.assertNotIn("Please make sure", error)


if __name__ == "__main__":
    unittest.main()
