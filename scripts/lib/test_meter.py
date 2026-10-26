import math
import unittest
from dataclasses import dataclass, field

import meter
from meter import Reading

MINUTE = 60.0
WINDOW = 300  # minutes in a 5-hour window
REACTION = 8.0


def load(agents: int, **options) -> meter.Load:
    """Agents that have run since long before the readings."""
    return meter.Load(agents, REACTION, 1.0, tuple((-100_000.0, None) for _ in range(agents)), **options)


@dataclass
class Agent:
    burn: float  # percent per minute
    minutes_left: float
    stops_in: float | None = None  # minutes until it stops after a wind-down notice
    started: float = 0.0
    stopped: float | None = None


@dataclass
class Simulation:
    """An account over one window: agents use it at their own rates, and readings come in whole percents."""

    agent_burn: float
    issue_minutes: float
    issues: int
    reading_every: int = 1
    max_agents: int = 20
    readings_stop_at: int | None = None
    may_stop_early: bool = True
    used: float = 0.0
    agents: list[Agent] = field(default_factory=list)
    finished: list[Agent] = field(default_factory=list)
    readings: list[Reading] = field(default_factory=list)
    peak: float = 0.0
    wind_downs: int = 0
    last_start: float = -1.0
    last_stop: float = -math.inf

    def run(self, minutes: int = WINDOW, burn_change: tuple[int, float] | None = None) -> "Simulation":
        for minute in range(minutes):
            now = minute * MINUTE
            if burn_change and minute == burn_change[0]:
                for agent in self.agents:
                    agent.burn *= burn_change[1]
                self.agent_burn *= burn_change[1]
            self.read(minute, now)
            running = [agent for agent in self.agents if agent.stops_in is None]
            measured = not self.readings or self.readings[-1].time > self.last_start
            runs = tuple((agent.started, agent.stopped) for agent in self.agents + self.finished)
            load = meter.Load(len(running), REACTION, 1.0, runs, measured_since_last_start=measured)
            decision = meter.decide({"5h": self.readings}, now, load)
            if self.may_stop_early and self.can_stop_early(decision):
                break
            self.act(decision, now)
            self.work(now)
        return self

    def can_stop_early(self, decision: meter.Decision) -> bool:
        """True when the rest of the window can't change what the tests check. Once no agent has run for the longest
        burn span, the readings stop changing, so the meter repeats its decision. Stopping there saves the meter's
        tests 3.4 s of CPU."""
        # Readings that stop coming age, and the meter's decisions change as they do.
        if self.agents or self.readings_stop_at is not None:
            return False
        if self.issues == 0:
            return True
        is_past_longest_burn_span = self.readings[-1].time - self.last_stop >= max(meter.BURN_SPANS_MINUTES) * MINUTE
        return is_past_longest_burn_span and decision.action != "start"

    def read(self, minute: int, now: float) -> None:
        fresh = self.readings_stop_at is None or minute < self.readings_stop_at
        if fresh and minute % self.reading_every == 0:
            self.readings.append(Reading(now, math.floor(self.used), WINDOW * MINUTE))

    def act(self, decision: meter.Decision, now: float) -> None:
        if decision.action == "start":
            for _ in range(min(decision.room, self.issues, self.max_agents - len(self.agents))):
                self.agents.append(Agent(self.agent_burn, self.issue_minutes, started=now))
                self.issues -= 1
                self.last_start = now
        running = [agent for agent in self.agents if agent.stops_in is None]
        for agent in running[len(running) - decision.wind_down :] if decision.wind_down else []:
            agent.stops_in = REACTION
            self.wind_downs += 1

    def work(self, now: float) -> None:
        for agent in self.agents:
            self.used += agent.burn
            agent.minutes_left -= 1
            if agent.stops_in is not None:
                agent.stops_in -= 1
            if agent.minutes_left <= 0 or (agent.stops_in is not None and agent.stops_in <= 0):
                agent.stopped = now + MINUTE
                self.last_stop = agent.stopped
                self.finished.append(agent)
        self.agents = [a for a in self.agents if a.stopped is None]
        self.peak = max(self.peak, self.used)


class WindDownTest(unittest.TestCase):
    def test_stopping_early_gives_the_results_of_the_whole_window(self):
        # With 5 issues the simulation runs out of issues. With 500 it never does, so the usage limit decides when
        # agents stop.
        for issues in (5, 500):
            early = Simulation(agent_burn=0.4, issue_minutes=40, issues=issues, reading_every=5).run()
            whole = Simulation(
                agent_burn=0.4, issue_minutes=40, issues=issues, reading_every=5, may_stop_early=False
            ).run()
            with self.subTest(issues=issues):
                self.assertEqual(
                    (early.peak, early.wind_downs, early.issues), (whole.peak, whole.wind_downs, whole.issues)
                )

    def test_twenty_fast_agents_stop_close_to_the_limit_without_crossing_it(self):
        simulation = Simulation(agent_burn=0.05, issue_minutes=40, issues=500).run()
        self.assertLess(simulation.peak, 100)
        self.assertGreater(simulation.peak, 90)

    def test_one_slow_agent_never_winds_down(self):
        simulation = Simulation(agent_burn=0.05, issue_minutes=600, issues=1).run()
        self.assertEqual(simulation.wind_downs, 0)

    def test_does_not_slow_down_while_agents_finish_on_their_own(self):
        simulation = Simulation(agent_burn=0.05, issue_minutes=30, issues=40).run()
        self.assertEqual(simulation.wind_downs, 0)
        self.assertEqual(simulation.issues, 0)

    def test_a_sudden_rise_in_burn_still_stops_before_the_limit(self):
        simulation = Simulation(agent_burn=0.03, issue_minutes=60, issues=500).run(burn_change=(60, 3.0))
        self.assertLess(simulation.peak, 100)

    def test_stale_readings_wind_the_agents_down(self):
        simulation = Simulation(agent_burn=0.05, issue_minutes=600, issues=30, readings_stop_at=60).run()
        self.assertLess(simulation.peak, 100)
        self.assertGreater(simulation.wind_downs, 0)

    def test_short_and_long_issues_with_fast_and_slow_agents_stop_before_the_limit(self):
        # Readings in whole percents, taken every minute or every five, as the orchestrator's status line and the
        # probes give them.
        for burn in (0.02, 0.1, 0.2, 0.4):
            for minutes in (15, 40):
                for every in (1, 5):
                    simulation = Simulation(agent_burn=burn, issue_minutes=minutes, issues=500, reading_every=every)
                    with self.subTest(burn=burn, minutes=minutes, every=every):
                        self.assertLess(simulation.run().peak, 100)

    def test_readings_every_five_minutes_still_stop_before_the_limit(self):
        simulation = Simulation(agent_burn=0.05, issue_minutes=40, issues=500, reading_every=5).run()
        self.assertLess(simulation.peak, 100)
        self.assertGreater(simulation.peak, 88)


class DecideTest(unittest.TestCase):
    def test_starts_on_an_account_with_no_readings_or_a_reset_window(self):
        self.assertEqual(meter.decide({}, 0, load(0)).action, "start")
        reset = [Reading(0, 99, 100)]
        self.assertEqual(meter.decide({"5h": reset}, 200, load(3)).action, "start")

    def test_the_strictest_window_decides(self):
        five_hour = [Reading(0, 10, 10_000), Reading(600, 12, 10_000)]
        weekly = [Reading(0, 96.06, 900_000), Reading(600, 96.56, 900_000)]
        self.assertEqual(meter.decide({"5h": five_hour, "7d": weekly}, 600, load(2)).action, "wind down")

    def test_starts_no_more_agents_until_a_reading_shows_what_the_last_ones_use(self):
        readings = [Reading(0, 10, 10_000), Reading(600, 11, 10_000)]
        waiting = load(2, measured_since_last_start=False)
        self.assertEqual(meter.decide({"5h": readings}, 600, waiting).action, "hold")
        self.assertEqual(meter.decide({"5h": readings}, 600, load(2)).action, "start")

    def test_starts_no_agent_once_the_latest_reading_is_stale(self):
        readings = [Reading(0, 50, 10_000), Reading(600, 51, 10_000)]
        fresh_until = 600 + meter.STALE_MINUTES * MINUTE
        self.assertEqual(meter.decide({"5h": readings}, fresh_until, load(1)).action, "start")
        self.assertEqual(meter.decide({"5h": readings}, fresh_until + MINUTE, load(1)).action, "hold")

    def test_agents_that_started_after_the_readings_count_at_the_rate_of_those_in_them(self):
        # One agent used 0.5% a minute; two more started after the latest reading.
        readings = [Reading(0, 80, 10_000), Reading(600, 85, 10_000)]
        runs = ((-1_000.0, None), (650.0, None), (650.0, None))
        self.assertEqual(meter.decide({"5h": readings}, 660, meter.Load(3, REACTION, 1.0, runs)).action, "pause")

    def test_agents_whose_use_doesnt_show_in_whole_percents_keep_running(self):
        # Ten slow agents on a weekly window that hasn't moved for 20 minutes.
        readings = [Reading(0, 90, 900_000), Reading(1_200, 90, 900_000)]
        runs = tuple((-5_000.0, None) for _ in range(10))
        decision = meter.decide({"7d": readings}, 1_200, meter.Load(10, REACTION, 1.0, runs))
        self.assertEqual(decision.action, "start")

    def test_an_agent_whose_use_hasnt_shown_yet_counts_at_one_percent_over_the_span(self):
        # One agent ran through five minutes in which the whole-percent reading didn't move.
        readings = [Reading(0, 92, 10_000), Reading(300, 92, 10_000)]
        decision = meter.decide({"5h": readings}, 300, meter.Load(1, REACTION, 1.0, ((0.0, None),)))
        self.assertEqual((decision.action, decision.burn), ("hold", 0.2))

    def test_use_that_hasnt_shown_counts_at_one_percent_over_the_longest_span_without_a_rise(self):
        # Ten agents ran through readings, taken every five minutes, that didn't move for 20.
        readings = [Reading(time, 95, 900_000) for time in range(0, 1_201, 300)]
        runs = tuple((-5_000.0, None) for _ in range(10))
        decision = meter.decide({"7d": readings}, 1_200, meter.Load(10, REACTION, 1.0, runs))
        self.assertEqual((decision.action, decision.burn), ("start", 0.05))

    def test_agents_count_at_the_rate_of_a_recent_rise(self):
        # One agent used 1% in 15 minutes, then 2% in the next 5; two more started after the latest reading.
        readings = [Reading(0, 88, 10_000), Reading(900, 89, 10_000), Reading(1_200, 91, 10_000)]
        runs = ((-1_000.0, None), (1_250.0, None), (1_250.0, None))
        self.assertEqual(meter.decide({"5h": readings}, 1_260, meter.Load(3, REACTION, 1.0, runs)).action, "pause")

    def test_after_a_pause_agents_count_at_the_rate_measured_before_it(self):
        # One agent used 0.5% a minute and stopped; 40 quiet minutes later, two agents started.
        quiet = [Reading(time, 80, 10_000) for time in (1_200, 1_800, 2_400, 3_000)]
        readings = [Reading(0, 75, 10_000), Reading(600, 80, 10_000), *quiet]
        runs = ((-1_000.0, 600.0), (2_950.0, None), (2_950.0, None))
        self.assertEqual(meter.decide({"5h": readings}, 3_000, meter.Load(2, REACTION, 1.0, runs)).action, "hold")

    def test_a_reading_of_another_window_among_the_readings_is_left_out(self):
        stray = Reading(300, 10, 5_000)
        readings = [Reading(0, 70, 10_000), stray, Reading(600, 75, 10_000)]
        self.assertEqual(meter.burn_rate(readings), 0.5)

    def test_a_reading_a_hair_below_the_one_before_tells_no_agent_to_stop(self):
        # A status-line reading of 57, then an agent's 0.57 of the window times 100.
        readings = [Reading(0, 57, 900_000), Reading(300, 0.57 * 100, 900_000)]
        runs = ((-5_000.0, None), (-5_000.0, None))
        decision = meter.decide({"7d": readings}, 300, meter.Load(2, REACTION, 1.0, runs))
        self.assertEqual(decision.wind_down, 0)

    def test_an_agent_that_just_started_isnt_charged_with_the_accounts_other_use(self):
        # The orchestrator uses 0.2% a minute of its own account; an agent started there 10 seconds ago.
        readings = [Reading(0, 69, 10_000), Reading(300, 70, 10_000)]
        decision = meter.decide({"5h": readings}, 300, meter.Load(1, REACTION, 1.0, ((290.0, None),)))
        self.assertEqual(decision.action, "start")

    def test_starts_no_agent_that_would_have_to_stop_soon(self):
        # One agent at 0.5% a minute, at 91%: two agents would reach the stop point in 6 minutes.
        readings = [Reading(0, 86, 10_000), Reading(600, 91, 10_000)]
        self.assertEqual(meter.decide({"5h": readings}, 600, load(1)).action, "hold")

    def test_starts_an_agent_with_a_tenth_of_the_window_left_and_nothing_running(self):
        readings = [Reading(0, 88, 10_000), Reading(600, 88, 10_000)]
        self.assertEqual(meter.decide({"5h": readings}, 600, load(0)).action, "start")

    def test_a_reserve_holds_the_account_back_earlier(self):
        readings = [Reading(0, 80, 10_000), Reading(600, 82, 10_000)]
        self.assertEqual(meter.decide({"5h": readings}, 600, load(2)).action, "start")
        self.assertEqual(meter.decide({"5h": readings}, 600, load(2, reserve=15)).action, "pause")


if __name__ == "__main__":
    unittest.main()
