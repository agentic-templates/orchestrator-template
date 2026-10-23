import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_an_agent_cant_read_secrets_or_account_folders_but_can_read_what_gh_and_git_need(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = Path(folder.name)
        for name in ("gh", "git", "anthropic"):
            (home / ".config" / name).mkdir(parents=True)
        accounts = home / "accounts.json"
        accounts.write_text(json.dumps([{"name": "claude-2", "provider": "claude", "home": str(home / ".claude-2")}]))
        with mock.patch.object(Path, "home", lambda: home), mock.patch.object(launching.usage, "ACCOUNTS", accounts):
            settings = json.loads(launching.settings())
        denied = settings["permissions"]["deny"]
        expected = [
            "Read(~/.netrc)",
            "Read(~/.config/git/credentials)",
            "Read(~/.config/anthropic/**)",
            "Read(~/.claude-2/**)",
            "Read(~/.claude.json)",
        ]
        for rule in expected:
            self.assertIn(rule, denied)
        self.assertFalse([rule for rule in denied if "/.config/gh" in rule or rule.startswith("Read(~/.config/git/**")])
        self.assertIn({"path": "~/.claude-2", "mode": "deny"}, settings["sandbox"]["credentials"]["files"])

    def test_an_agent_cant_read_the_files_the_project_denies_in_any_folder_of_the_main_checkout(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        main = Path(folder.name)
        (main / ".claude").mkdir()
        project = {"permissions": {"deny": ["Read(**/.env)", "Read(~/.ssh/**)"]}, "sandbox": {}}
        (main / ".claude" / "settings.json").write_text(json.dumps(project))
        with mock.patch.object(launching, "ROOT", main):
            denied = json.loads(launching.settings())["permissions"]["deny"]
        in_main = [rule for rule in denied if rule.startswith(f"Read(/{main}/")]
        self.assertEqual(in_main, [f"Read(/{main}/**/.env)"])


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class NestedRepositoryTest(unittest.TestCase):
    """A builder's worktree, in which the agent planted a nested repository whose config runs a program that leaves a
    marker, as an agent can from inside the sandbox."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        run_git(root, "init", "--quiet", "--bare", "origin.git")
        run_git(root, "clone", "--quiet", str(root / "origin.git"), "main")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com"), ("commit.gpgsign", "false")):
            run_git(root / "main", "config", key, value)
        run_git(root / "main", "commit", "--quiet", "--allow-empty", "-m", "start")
        run_git(root / "main", "push", "--quiet", "origin", "HEAD:main")
        self.main = root / "main"
        self.worktree = root / "worktree"
        run_git(self.main, "worktree", "add", "--quiet", "-b", "issue-1", str(self.worktree))
        self.marker = root / "marker"
        self.nested = self.worktree / "vendor" / "repo"
        self.nested.mkdir(parents=True)
        run_git(root, "init", "--quiet", "--separate-git-dir", str(root / "planted"), str(self.nested))
        run_git(self.nested, "config", "filter.evil.clean", f"touch {self.marker}; cat")
        (self.nested / ".gitattributes").write_text("* filter=evil\n")
        (self.nested / "file").write_text("planted\n")
        (self.worktree / "new.py").write_text("print('work')\n")

    def committed(self) -> list[str]:
        return run_git(self.worktree, "ls-tree", "-r", "--name-only", "origin/issue-1").split()

    def test_an_untracked_nested_repository_is_left_out_and_the_rest_saved(self):
        self.assertEqual(launching.save_work(self.worktree), "")
        self.assertEqual(self.committed(), ["new.py"])
        self.assertFalse(self.marker.exists())

    def stage_nested(self) -> None:
        """Commits the nested repository into the worktree as a gitlink, then changes it."""
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com"), ("commit.gpgsign", "false")):
            run_git(self.nested, "config", key, value)
        run_git(self.nested, "add", "--all")
        run_git(self.nested, "commit", "--quiet", "-m", "start")
        run_git(self.worktree, "add", "-f", "vendor/repo")
        run_git(self.worktree, "commit", "--quiet", "-m", "add the repository")
        self.marker.unlink(missing_ok=True)
        (self.nested / "file").write_text("changed\n")

    def test_a_nested_repository_the_agent_staged_runs_nothing_when_the_work_is_saved_and_removed(self):
        self.stage_nested()
        self.assertEqual(launching.save_work(self.worktree), "")
        self.assertIn("new.py", self.committed())
        with mock.patch.dict(launching.git.__kwdefaults__, {"cwd": self.main}):
            launching.remove_worktree(self.worktree)
        self.assertFalse(self.worktree.exists())
        self.assertFalse(self.marker.exists())

    def test_a_nested_repository_the_agent_staged_runs_nothing_when_git_reads_the_worktree(self):
        self.stage_nested()
        run_git(self.nested, "config", "core.fsmonitor", f"touch {self.marker}; false")
        launching.worktree_git(self.worktree, "status", "--porcelain")
        self.assertFalse(self.marker.exists())

    def test_a_worktree_with_only_a_nested_repository_has_no_changes(self):
        (self.worktree / "new.py").unlink()
        self.assertEqual(launching.changes(self.worktree, launching.nested_repositories(self.worktree)), "")

    def test_the_report_names_what_was_left_out(self):
        with mock.patch.object(launching.github, "gh_json", return_value=[]):
            report = launching.saved_work(self.worktree)
        self.assertIn("vendor/repo is a nested git repository, so it wasn't saved", report)


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


class HandleTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.note = Path(folder.name) / "issue-7.md"
        self.note.write_text("Left: the tests.")
        self.session = launching.Session("builder-7-abc", "id", "claude", Path(folder.name), None, Path())
        self.session.note = self.note

    def test_the_note_is_kept_until_the_agent_answers(self):
        launching.handle(self.session, b'{"type": "system", "subtype": "init"}')
        self.assertTrue(self.note.exists())
        launching.handle(self.session, b'{"type": "assistant", "message": {"model": "claude-haiku-5-5"}}')
        self.assertFalse(self.note.exists())

    def test_the_note_is_kept_when_the_first_request_fails(self):
        launching.handle(self.session, b'{"type": "assistant", "error": "rate_limit"}')
        launching.handle(self.session, b'{"type": "assistant", "message": {"model": "<synthetic>"}}')
        self.assertTrue(self.note.exists())

    def test_a_result_saves_each_models_context_window(self):
        with mock.patch.object(launching.usage, "CONTEXT_WINDOWS", self.note.parent / "context-windows.json"):
            result = {"type": "result", "result": "Done.", "modelUsage": {"claude-x": {"contextWindow": 200_000}}}
            launching.handle(self.session, json.dumps(result).encode())
            self.assertEqual(launching.usage.context_window("claude", "claude-x"), 200_000)

    def test_a_note_written_later_is_kept(self):
        launching.handle(self.session, b'{"type": "assistant"}')
        self.note.write_text("Left: the docs.")
        launching.handle(self.session, b'{"type": "assistant"}')
        self.assertTrue(self.note.exists())


class CollectNoteTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.worktree = self.root / "builder-7-abc"
        (self.worktree / ".loop").mkdir(parents=True)
        self.secret = self.root / "secret"
        self.secret.write_text("a password")
        patch = mock.patch.object(launching.usage, "HANDOFF", self.root / "handoff")
        patch.start()
        self.addCleanup(patch.stop)

    def collected(self) -> str | None:
        launching.collect_note(self.worktree, 7)
        note = self.root / "handoff" / "issue-7.md"
        return note.read_text() if note.exists() else None

    def test_a_note_moves_under_the_issue(self):
        (self.worktree / ".loop" / "handoff.md").write_text("Left: the tests.")
        self.assertEqual(self.collected(), "Left: the tests.")
        self.assertFalse((self.worktree / ".loop" / "handoff.md").exists())

    def test_no_note(self):
        self.assertIsNone(self.collected())

    def test_a_link_to_another_file_is_ignored(self):
        (self.worktree / ".loop" / "handoff.md").symlink_to(self.secret)
        self.assertIsNone(self.collected())

    def test_a_hard_link_to_another_file_is_ignored(self):
        os.link(self.secret, self.worktree / ".loop" / "handoff.md")
        self.assertIsNone(self.collected())

    def test_a_named_pipe_is_ignored(self):
        os.mkfifo(self.worktree / ".loop" / "handoff.md")
        self.assertIsNone(self.collected())

    def test_a_folder_is_ignored(self):
        (self.worktree / ".loop" / "handoff.md").mkdir()
        self.assertIsNone(self.collected())

    def test_text_that_isnt_utf_8_is_kept_readable(self):
        (self.worktree / ".loop" / "handoff.md").write_bytes(b"\xff\xfeLeft: the tests.")
        self.assertTrue(self.collected().endswith("Left: the tests."))

    def test_a_folder_swapped_for_a_link_while_the_note_is_read_keeps_the_other_file(self):
        (self.worktree / ".loop" / "handoff.md").write_text("Left: the tests.")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "handoff.md").write_text("a password")
        read = launching.plain_file_text

        def read_then_swap(folder: int, name: str) -> str | None:
            text = read(folder, name)
            (self.worktree / ".loop").rename(self.root / "moved")
            (self.worktree / ".loop").symlink_to(elsewhere)
            return text

        # The swap must land between the read and the delete, and only the project's own code runs between them.
        with mock.patch.object(launching, "plain_file_text", read_then_swap):
            self.assertEqual(self.collected(), "Left: the tests.")
        self.assertTrue((elsewhere / "handoff.md").exists())

    def test_a_linked_folder_is_ignored(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "handoff.md").write_text("a password")
        (self.worktree / ".loop").rmdir()
        (self.worktree / ".loop").symlink_to(elsewhere)
        self.assertIsNone(self.collected())


class NoteForTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(launching.usage, "HANDOFF", Path(folder.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.note = Path(folder.name) / "issue-7.md"
        self.note.write_text("Left: the tests.\nRun them with make test.")

    def test_a_builder_that_continues_gets_the_note_quoted_and_the_commits(self):
        note = launching.note_for(7, continues=True)
        self.assertEqual(note, self.note)
        message = launching.successor_message(7, note, "abc1234 feat: add the parser")
        self.assertIn("\n\n> Left: the tests.\n> Run them with make test.\n\n", message)
        self.assertTrue(message.endswith("\n\nabc1234 feat: add the parser"))

    def test_a_builder_that_starts_from_main_deletes_the_note(self):
        self.assertIsNone(launching.note_for(7, continues=False))
        self.assertFalse(self.note.exists())

    def test_a_successor_without_a_note_or_commits_is_told_so(self):
        message = launching.successor_message(7, None, "")
        self.assertIn("without a hand-off note", message)
        self.assertIn("no commits yet", message)


if __name__ == "__main__":
    unittest.main()
