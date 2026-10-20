import math
import unittest
from dataclasses import dataclass, field

import meter
from meter import Reading

MINUTE = 60.0
WINDOW = 300  # minutes in a 5-hour window
REACTION = 8.0


@dataclass
class Agent:
    burn: float  # percent per minute
    minutes_left: float
    stops_in: float | None = None  # minutes until it stops after a wind-down notice


@dataclass
class Simulation:
    """An account over one window: agents use it at their own rates, and readings come in whole percents."""

    agent_burn: float
    issue_minutes: float
    issues: int
    reading_every: int = 1
    max_agents: int = 20
    readings_stop_at: int | None = None
    used: float = 0.0
    agents: list[Agent] = field(default_factory=list)
    readings: list[Reading] = field(default_factory=list)
    peak: float = 0.0
    wind_downs: int = 0
    last_start: float = -1.0

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
            load = meter.Load(len(running), REACTION, 1.0, measured_since_last_start=measured)
            self.act(meter.decide({"5h": self.readings}, now, load), now)
            self.work()
        return self

    def read(self, minute: int, now: float) -> None:
        fresh = self.readings_stop_at is None or minute < self.readings_stop_at
        if fresh and minute % self.reading_every == 0:
            self.readings.append(Reading(now, math.floor(self.used), WINDOW * MINUTE))

    def act(self, decision: meter.Decision, now: float) -> None:
        if decision.action == "start":
            for _ in range(min(decision.room, self.issues, self.max_agents - len(self.agents))):
                self.agents.append(Agent(self.agent_burn, self.issue_minutes))
                self.issues -= 1
                self.last_start = now
        running = [agent for agent in self.agents if agent.stops_in is None]
        for agent in running[len(running) - decision.wind_down :] if decision.wind_down else []:
            agent.stops_in = REACTION
            self.wind_downs += 1

    def work(self) -> None:
        for agent in self.agents:
            self.used += agent.burn
            agent.minutes_left -= 1
            if agent.stops_in is not None:
                agent.stops_in -= 1
        self.agents = [a for a in self.agents if a.minutes_left > 0 and (a.stops_in is None or a.stops_in > 0)]
        self.peak = max(self.peak, self.used)


class WindDownTest(unittest.TestCase):
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

    def test_readings_every_five_minutes_still_stop_before_the_limit(self):
        simulation = Simulation(agent_burn=0.05, issue_minutes=40, issues=500, reading_every=5).run()
        self.assertLess(simulation.peak, 100)
        self.assertGreater(simulation.peak, 88)


class DecideTest(unittest.TestCase):
    def test_starts_on_an_account_with_no_readings_or_a_reset_window(self):
        self.assertEqual(meter.decide({}, 0, meter.Load(0, REACTION, 1.0)).action, "start")
        reset = [Reading(0, 99, 100)]
        self.assertEqual(meter.decide({"5h": reset}, 200, meter.Load(3, REACTION, 1.0)).action, "start")

    def test_the_strictest_window_decides(self):
        five_hour = [Reading(0, 10, 10_000), Reading(600, 12, 10_000)]
        weekly = [Reading(0, 97.06, 900_000), Reading(600, 97.56, 900_000)]
        self.assertEqual(
            meter.decide({"5h": five_hour, "7d": weekly}, 600, meter.Load(2, REACTION, 1.0)).action, "wind down"
        )

    def test_starts_no_more_agents_until_a_reading_shows_what_the_last_ones_use(self):
        readings = [Reading(0, 10, 10_000), Reading(600, 11, 10_000)]
        waiting = meter.Load(2, REACTION, 1.0, measured_since_last_start=False)
        self.assertEqual(meter.decide({"5h": readings}, 600, waiting).action, "hold")
        self.assertEqual(meter.decide({"5h": readings}, 600, meter.Load(2, REACTION, 1.0)).action, "start")

    def test_starts_no_agent_that_would_have_to_stop_soon(self):
        # At 0.5% a minute, the account reaches its stop point in about 14 minutes, too soon for a new agent.
        readings = [Reading(0, 86, 10_000), Reading(600, 91, 10_000)]
        self.assertEqual(meter.decide({"5h": readings}, 600, meter.Load(1, REACTION, 1.0)).action, "hold")

    def test_a_reserve_holds_the_account_back_earlier(self):
        readings = [Reading(0, 80, 10_000), Reading(600, 82, 10_000)]
        self.assertEqual(meter.decide({"5h": readings}, 600, meter.Load(2, REACTION, 1.0)).action, "start")
        self.assertEqual(meter.decide({"5h": readings}, 600, meter.Load(2, REACTION, 1.0, reserve=15)).action, "pause")


if __name__ == "__main__":
    unittest.main()
