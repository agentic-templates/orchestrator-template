import unittest

import meter
import metering


class ReportTest(unittest.TestCase):
    def test_an_account_without_a_reading_says_so(self):
        result = {"codex": meter.Decision("hold", 0, 0, float("nan"), 0.0)}
        self.assertEqual(metering.report_line("codex", result["codex"]), "codex: hold (no reading yet)")
        self.assertNotIn("nan", metering.report(result))


if __name__ == "__main__":
    unittest.main()
