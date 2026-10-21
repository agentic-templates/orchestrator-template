import os
import subprocess
import unittest

import usage


class AgentSkillsTest(unittest.TestCase):
    def test_reads_the_skills_an_agent_type_preloads(self):
        self.assertIn("building", usage.agent_skills("builder-small"))
        self.assertNotIn("building", usage.agent_skills("reviewer"))

    def test_an_unknown_agent_type_preloads_nothing(self):
        self.assertEqual(usage.agent_skills("no-such-agent"), [])


class RunsElsewhereTest(unittest.TestCase):
    def test_a_live_orchestrator_in_another_session_runs_elsewhere(self):
        self.assertTrue(usage.runs_elsewhere(os.getpid(), mine=1))

    def test_the_recorded_orchestrator_is_this_session(self):
        self.assertFalse(usage.runs_elsewhere(os.getpid(), mine=os.getpid()))

    def test_an_orchestrator_that_stopped_doesnt_run(self):
        finished = subprocess.Popen(["true"])
        finished.wait()
        self.assertFalse(usage.runs_elsewhere(finished.pid, mine=None))

    def test_no_recorded_orchestrator(self):
        self.assertFalse(usage.runs_elsewhere(None, mine=None))


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


if __name__ == "__main__":
    unittest.main()
