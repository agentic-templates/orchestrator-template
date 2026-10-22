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

import usage


class AgentSkillsTest(unittest.TestCase):
    def test_reads_the_skills_an_agent_type_preloads(self):
        self.assertIn("building", usage.agent_skills("builder-small"))
        self.assertNotIn("building", usage.agent_skills("reviewer"))

    def test_an_unknown_agent_type_preloads_nothing(self):
        self.assertEqual(usage.agent_skills("no-such-agent"), [])


class RunsElsewhereTest(unittest.TestCase):
    def test_a_live_orchestrator_in_another_session_runs_elsewhere(self):
        self.assertTrue(usage.runs_elsewhere(os.getpid(), usage.started_at(os.getpid()), mine=1))

    def test_a_later_process_with_the_same_id_isnt_the_orchestrator(self):
        self.assertFalse(usage.runs_elsewhere(os.getpid(), "Mon Oct 20 09:00:00 2026", mine=1))

    def test_the_recorded_orchestrator_is_this_session(self):
        self.assertFalse(usage.runs_elsewhere(os.getpid(), usage.started_at(os.getpid()), mine=os.getpid()))

    def test_an_orchestrator_that_stopped_doesnt_run(self):
        finished = subprocess.Popen(["true"])
        finished.wait()
        self.assertFalse(usage.runs_elsewhere(finished.pid, "Mon Oct 20 09:00:00 2026", mine=None))

    def test_no_recorded_orchestrator(self):
        self.assertFalse(usage.runs_elsewhere(None, None, mine=None))


class IsRunningTest(unittest.TestCase):
    def test_a_live_process_of_the_program(self):
        self.assertTrue(usage.is_running(os.getpid(), "unittest"))

    def test_a_live_process_of_another_program(self):
        self.assertFalse(usage.is_running(os.getpid(), "launch"))

    def test_the_program_counts_whatever_path_started_it(self):
        started = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(started.wait)
        self.addCleanup(started.kill)
        self.assertTrue(usage.is_running(started.pid, "sleep"))


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
        self.addCleanup(child.kill)
        launcher = subprocess.Popen(["true"])
        launcher.wait()
        record = {"id": "builder-1-test", "pid": launcher.pid, "child": child.pid, "started": 0}
        (usage.AGENTS / "builder-1-test.json").write_text(json.dumps(record))
        return child

    def test_an_agent_without_its_launcher_is_ended_and_stops_counting(self):
        child = self.orphan("import time; time.sleep(30)")
        self.assertEqual(usage.running_agents(), [])
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)
        self.assertIn("stopped", json.loads((usage.AGENTS / "builder-1-test.json").read_text()))

    def test_an_agent_that_ignores_being_told_to_stop_is_killed(self):
        child = self.orphan("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)")
        # Gives Python the time to start ignoring the signal.
        time.sleep(0.2)
        with mock.patch.object(usage, "STOP_SECONDS", 0.3):
            self.assertEqual(usage.running_agents(), [])
        self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)

    def test_an_agent_that_ends_just_before_it_is_told_to_stop_counts_as_stopped(self):
        self.orphan("import time; time.sleep(30)")
        # The signal finds no process, as when the agent ends between the check and the signal.
        with mock.patch.object(usage.os, "kill", side_effect=ProcessLookupError):
            self.assertEqual(usage.running_agents(), [])
        self.assertIn("stopped", json.loads((usage.AGENTS / "builder-1-test.json").read_text()))


if __name__ == "__main__":
    unittest.main()
