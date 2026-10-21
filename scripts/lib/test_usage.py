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


if __name__ == "__main__":
    unittest.main()
