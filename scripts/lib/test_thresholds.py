import unittest

from thresholds import Context, best_point, compute

CLAUDE = Context(window=1_000_000, uncontrolled=1_000_000)
# Codex compacts by itself at 90% of its window, raised to 872,000 tokens.
CODEX = Context(window=872_000, uncontrolled=784_800, write_to_read=10.0)
HAIKU_ON_THE_API = Context(window=1_000_000, uncontrolled=1_000_000, price_boundary=100_000, price_multiplier=5.0)


class BestPointTest(unittest.TestCase):
    def test_matches_the_measurement_of_the_earlier_project(self):
        # zzz measured a successor's start at about 227k and a best hand-off at about 470k.
        self.assertAlmostEqual(best_point(227_000, 4_200, 31.0), 470_000, delta=10_000)


class ComputeTest(unittest.TestCase):
    def test_orders_every_threshold_below_what_the_loop_doesnt_control(self):
        for context in (CLAUDE, CODEX, HAIKU_ON_THE_API):
            for start in (30_000, 60_000, 120_000):
                for growth in (1_000, 4_000, 20_000):
                    t = compute(context, start, growth)
                    self.assertLess(start, t.refresh)
                    self.assertLess(t.refresh, t.while_active)
                    self.assertLess(t.while_active, t.emergency)
                    self.assertLess(t.emergency, context.uncontrolled)

    def test_puts_the_emergency_stop_at_a_share_of_the_first_point_outside_the_loops_control(self):
        self.assertEqual(compute(CLAUDE, 60_000, 4_000).emergency, 800_000)
        self.assertEqual(compute(CODEX, 60_000, 4_000).emergency, 627_840)

    def test_moves_before_a_price_boundary_only_when_the_price_rises(self):
        self.assertLess(compute(HAIKU_ON_THE_API, 60_000, 4_000).refresh, 100_000)
        self.assertGreater(compute(CLAUDE, 60_000, 4_000).refresh, 100_000)

    def test_refuses_a_window_too_small_for_the_thresholds(self):
        tiny = Context(window=120_000, uncontrolled=108_000)
        with self.assertRaises(ValueError):
            compute(tiny, 90_000, 4_000)


if __name__ == "__main__":
    unittest.main()
