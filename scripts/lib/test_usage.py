import unittest

import usage


class AgentSkillsTest(unittest.TestCase):
    def test_reads_the_skills_an_agent_type_preloads(self):
        self.assertIn("building", usage.agent_skills("builder-small"))
        self.assertNotIn("building", usage.agent_skills("reviewer"))

    def test_an_unknown_agent_type_preloads_nothing(self):
        self.assertEqual(usage.agent_skills("no-such-agent"), [])


if __name__ == "__main__":
    unittest.main()
