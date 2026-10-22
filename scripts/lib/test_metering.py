import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
import metering
import usage


class ReportTest(unittest.TestCase):
    def test_an_account_without_a_reading_says_so(self):
        result = {"codex": meter.Decision("hold", 0, 0, float("nan"), 0.0)}
        self.assertEqual(metering.report_line("codex", result["codex"]), "codex: hold (no reading yet)")
        self.assertNotIn("nan", metering.report(result, []))


class WindDownTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for name, path in (("AGENTS", "agents"), ("NOTICES", "notices")):
            patch = mock.patch.object(usage, name, Path(folder.name) / path)
            patch.start()
            self.addCleanup(patch.stop)

    def test_agents_already_told_count_toward_those_to_tell(self):
        agents = [{"id": f"builder-{n}-test", "started": n} for n in range(4)]
        metering.tell_to_wind_down(agents, 2)
        metering.tell_to_wind_down(agents, 2)
        self.assertEqual(
            [agent["id"] for agent in agents if "told_to_wind_down" in agent], ["builder-2-test", "builder-3-test"]
        )
        metering.tell_to_wind_down(agents, 3)
        self.assertEqual(sum("told_to_wind_down" in agent for agent in agents), 3)


if __name__ == "__main__":
    unittest.main()
