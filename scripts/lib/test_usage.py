import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import metering
import usage


class AgentSkillsTest(unittest.TestCase):
    def test_reads_the_skills_an_agent_type_preloads(self):
        self.assertIn("building", usage.agent_skills("builder-small"))
        self.assertNotIn("building", usage.agent_skills("reviewer"))

    def test_an_unknown_agent_type_preloads_nothing(self):
        self.assertEqual(usage.agent_skills("no-such-agent"), [])

    def test_reads_skills_written_as_lines(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        agents = Path(folder.name) / ".claude" / "agents"
        agents.mkdir(parents=True)
        definition = "---\nname: builder-x\nskills:\n  - building\n  - code\nmodel: opus\n---\n"
        (agents / "builder-x.md").write_text(definition)
        with mock.patch.object(usage, "ROOT", Path(folder.name)):
            self.assertEqual(usage.agent_skills("builder-x"), ["building", "code"])
            self.assertEqual(usage.agent_config("builder-x"), "opus/default")


class RunsElsewhereTest(unittest.TestCase):
    def test_a_live_orchestrator_in_another_session_runs_elsewhere(self):
        self.assertTrue(usage.runs_elsewhere(os.getpid(), usage.started_at(os.getpid()), mine=1))

    def test_a_later_process_with_the_same_id_isnt_the_orchestrator(self):
        self.assertFalse(usage.runs_elsewhere(os.getpid(), "1792000000.000000", mine=1))

    def test_the_recorded_orchestrator_is_this_session(self):
        self.assertFalse(usage.runs_elsewhere(os.getpid(), usage.started_at(os.getpid()), mine=os.getpid()))

    def test_an_orchestrator_that_stopped_doesnt_run(self):
        finished = subprocess.Popen(["/bin/sleep", "30"])
        started = usage.started_at(finished.pid)
        finished.kill()
        finished.wait()
        self.assertFalse(usage.runs_elsewhere(finished.pid, started, mine=None))

    def test_no_recorded_orchestrator(self):
        self.assertFalse(usage.runs_elsewhere(None, None, mine=None))


class IsRunningTest(unittest.TestCase):
    def test_a_live_process_of_another_program(self):
        self.assertFalse(usage.is_running(os.getpid(), "launch"))

    def test_the_program_counts_whatever_path_started_it(self):
        started = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(started.wait)
        self.addCleanup(started.kill)
        self.assertTrue(usage.is_running(started.pid, "sleep"))

    def test_reads_a_process_when_ps_cant_start(self):
        started = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(started.wait)
        self.addCleanup(started.kill)
        # Without a PATH, ps can't start, as in the agents' sandbox.
        with mock.patch.dict(os.environ, {"PATH": ""}):
            self.assertTrue(usage.is_running(started.pid, "sleep"))
            self.assertTrue(usage.started_at(started.pid))


class ReserveTest(unittest.TestCase):
    def accounts(self, items: list[dict]) -> list[usage.Account]:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "accounts.json"
            path.write_text(json.dumps(items))
            with mock.patch.object(usage, "ACCOUNTS", path):
                return usage.accounts()

    def test_an_account_without_a_reserve_keeps_none(self):
        self.assertEqual(self.accounts([{"name": "claude", "provider": "claude"}])[0].reserve, 0.0)

    def test_a_reserve_below_the_whole_window_is_kept(self):
        self.assertEqual(self.accounts([{"name": "claude", "provider": "claude", "reserve": 99.9}])[0].reserve, 99.9)

    def test_a_negative_reserve_or_the_whole_window_is_refused(self):
        for reserve in (-5, 100):
            with self.subTest(reserve=reserve), self.assertRaises(SystemExit):
                self.accounts([{"name": "claude", "provider": "claude", "reserve": reserve}])


class CommandForTest(unittest.TestCase):
    def test_the_default_account_drops_a_config_folder_from_the_environment(self):
        account = usage.Account("claude", "claude", None)
        self.assertEqual(usage.command_for(account, ["claude"]), ["env", "-u", "CLAUDE_CONFIG_DIR", "claude"])

    def test_an_account_with_a_folder_runs_with_that_folder(self):
        account = usage.Account("claude-2", "claude", "/accounts/claude-2")
        self.assertEqual(
            usage.command_for(account, ["claude"]),
            ["env", "-u", "CLAUDE_CONFIG_DIR", "CLAUDE_CONFIG_DIR=/accounts/claude-2", "claude"],
        )

    def test_the_command_runs_on_the_account_whatever_it_inherits(self):
        account = usage.Account("claude", "claude", None)
        command = usage.command_for(account, ["sh", "-c", "echo ${CLAUDE_CONFIG_DIR-unset}"])
        inherited = os.environ | {"CLAUDE_CONFIG_DIR": "/accounts/claude-2"}
        output = subprocess.run(command, env=inherited, capture_output=True, text=True, check=True).stdout
        self.assertEqual(output, "unset\n")


class RunningAgentsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(usage, "AGENTS", Path(folder.name))
        patch.start()
        self.addCleanup(patch.stop)

    def orphan(self, agent_code: str) -> subprocess.Popen:
        """An agent's process, named by its ID as a launched agent is, whose launcher has ended."""
        child = subprocess.Popen([sys.executable, "-c", agent_code, "builder-1-test"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        launcher = subprocess.Popen(["true"])
        launcher.wait()
        record = {"id": "builder-1-test", "pid": launcher.pid, "child": child.pid, "started": 0}
        (usage.AGENTS / "builder-1-test.json").write_text(json.dumps(record))
        return child

    def test_an_agent_without_its_launcher_counts_as_running_until_it_is_ended(self):
        child = self.orphan("import time; time.sleep(30)")
        self.assertEqual([agent["id"] for agent in usage.running_agents()], ["builder-1-test"])
        self.assertIsNone(child.poll())
        usage.end_orphans()
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)
        self.assertEqual(usage.running_agents(), [])
        self.assertIn("stopped", json.loads((usage.AGENTS / "builder-1-test.json").read_text()))

    def test_an_agent_whose_launcher_and_session_have_gone_is_marked_as_stopped(self):
        child = self.orphan("pass")
        child.wait()
        self.assertEqual(usage.running_agents(), [])
        self.assertIn("stopped", json.loads((usage.AGENTS / "builder-1-test.json").read_text()))

    def test_an_agent_that_ignores_being_told_to_stop_is_killed(self):
        child = self.orphan("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)")
        # Gives Python the time to start ignoring the signal.
        time.sleep(0.2)
        with mock.patch.object(usage, "STOP_SECONDS", 0.3):
            usage.end_orphans()
        self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)

    def test_a_launcher_that_exits_while_an_orphan_is_ended_is_left_alone(self):
        orphan = self.orphan("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)")
        time.sleep(0.2)
        # A launcher that saved its record and exits before it starts the agent, such as when its worktree is busy.
        launcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)", "launch"])
        self.addCleanup(launcher.wait)
        record = {"id": "builder-2-test", "pid": launcher.pid, "started": 1}
        (usage.AGENTS / "builder-2-test.json").write_text(json.dumps(record))
        with mock.patch.object(usage, "STOP_SECONDS", 1.0):
            usage.end_orphans()
        self.assertEqual(orphan.wait(timeout=5), -signal.SIGKILL)
        self.assertNotIn("stopped", json.loads((usage.AGENTS / "builder-2-test.json").read_text()))

    def test_an_agent_that_ends_just_before_it_is_told_to_stop_counts_as_stopped(self):
        self.orphan("import time; time.sleep(30)")
        # The signal finds no process, as when the agent ends between the check and the signal.
        with mock.patch.object(usage.os, "kill", side_effect=ProcessLookupError):
            usage.end_orphans()
        self.assertEqual(usage.running_agents(), [])
        self.assertIn("stopped", json.loads((usage.AGENTS / "builder-1-test.json").read_text()))


class TempLoopTest(unittest.TestCase):
    """Points the loop's files at a fresh folder."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name, path in {
            "USAGE": self.root / "usage",
            "AGENTS": self.root / "agents",
            "NOTICES": self.root / "notices",
            "CONTEXT_WINDOWS": self.root / "context-windows.json",
            "ACCOUNTS": self.root / "accounts.json",
        }.items():
            patch = mock.patch.object(usage, name, path)
            patch.start()
            self.addCleanup(patch.stop)


class WindowsTest(TempLoopTest):
    def test_a_line_still_being_written_is_left_out(self):
        (self.root / "usage").mkdir()
        line = json.dumps({"time": time.time(), "window": "five_hour", "used": 40.0, "resets_at": 0})
        (self.root / "usage" / "claude.jsonl").write_text(line + '\n{"time": 17')
        self.assertEqual([r.used for r in usage.windows("claude")["five_hour"]], [40.0])


class AgentRecordsTest(TempLoopTest):
    def setUp(self):
        super().setUp()
        (self.root / "agents").mkdir()
        # A record that a failed launch deletes between the listing and the reading.
        (self.root / "agents" / "builder-1-gone.json").symlink_to(self.root / "deleted.json")

    def test_a_record_deleted_while_the_agents_are_listed_is_skipped(self):
        self.assertEqual(usage.running_agents(), [])

    def test_the_meter_skips_it_too(self):
        self.assertEqual(metering.recent_agents(0), [])


class NoticesTest(TempLoopTest):
    def test_a_notice_still_being_written_isnt_shown(self):
        (self.root / "notices" / "builder-1-a").mkdir(parents=True)
        (self.root / "notices" / "builder-1-a" / ".wind-down.x7k2").write_text("")
        self.assertEqual(usage.untold_notices("builder-1-a"), [])


class ContextWindowsTest(TempLoopTest):
    def test_a_window_is_known_for_the_account_once_a_turn_on_the_model_has_ended_there(self):
        self.assertIsNone(usage.context_window("claude", "claude-haiku-5-5"))
        usage.record_context_windows("claude", {"claude-haiku-5-5": {"contextWindow": 200_000}})
        usage.record_context_windows("claude-2", {"claude-haiku-5-5": {"contextWindow": 1_000_000}})
        self.assertEqual(usage.context_window("claude", "claude-haiku-5-5"), 200_000)
        self.assertEqual(usage.context_window("claude-2", "claude-haiku-5-5"), 1_000_000)


class TranscriptTest(TempLoopTest):
    def setUp(self):
        super().setUp()
        self.home = self.root / "claude-2-home"
        (self.root / "accounts.json").write_text(
            json.dumps([{"name": "claude-2", "provider": "claude", "home": str(self.home)}])
        )

    def test_is_found_whatever_claude_code_named_its_folder(self):
        folder = self.home / "projects" / "-Users-x-dev-my-project--loop-worktrees-builder-1-a1b2c3"
        folder.mkdir(parents=True)
        (folder / "session-1.jsonl").write_text("")
        self.assertEqual(usage.transcript("claude-2", "session-1"), folder / "session-1.jsonl")

    def test_a_session_that_hasnt_written_one_yet_has_none(self):
        self.assertIsNone(usage.transcript("claude-2", "session-1"))

    def test_an_account_that_isnt_listed_has_none(self):
        self.assertIsNone(usage.transcript("claude-3", "session-1"))


if __name__ == "__main__":
    unittest.main()
