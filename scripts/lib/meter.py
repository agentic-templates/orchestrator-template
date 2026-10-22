"""Decides from an account's usage whether to start agents on it, hold, or wind them down.

It looks ahead only as far as the agents need to reach their next stopping point, never to the window's reset,
so that a fast burn early in a window doesn't slow the loop down while there's still room.
"""

import math
from dataclasses import dataclass

# Every agent stops before an account's use is this close to its limit.
FLOOR = 2.0
# Minutes an agent takes from a wind-down notice to its next stopping point, until the loop has measured it.
DEFAULT_REACTION_MINUTES = 8.0
# Percent of a window per minute that one agent uses, until the account's own readings show it.
DEFAULT_AGENT_BURN = 0.1
# An agent starts only if it could work this long before the account would have to stop it, so that the cost of
# starting it, which writes its whole context to the cache, buys some real work. It doesn't need to finish,
# because a builder that has to stop saves its work for a successor.
MIN_RUN_MINUTES = 10.0
# No agent starts on a reading older than this, because the account may have moved since.
STALE_MINUTES = 10.0
# The burn rate is the larger of the rates over these spans of recent readings, so that a rise shows quickly
# and a fall only once it lasts.
BURN_SPANS_MINUTES = (5.0, 20.0)
# Readings are whole percents, rounded down, so the real use can be up to this much higher than a reading shows.
READING_STEP = 1.0
# A span of readings in which fewer agents than this ran, on average, mostly shows other use of the account, such as
# the orchestrator's, so it says nothing about an agent's rate.
MIN_AGENTS_IN_SPAN = 0.5


@dataclass(frozen=True)
class Reading:
    time: float  # seconds since the epoch
    used: float  # percent of the window
    resets_at: float  # seconds since the epoch


@dataclass(frozen=True)
class Decision:
    action: str  # "start", "hold", "wind down" or "pause"
    room: int  # how many more agents can start
    wind_down: int  # how many running agents to tell to stop at their next stopping point
    used: float  # percent, projected to now
    burn: float  # percent per minute


ORDER = ("start", "hold", "wind down", "pause")


def burn_rate(readings: list[Reading]) -> float | None:
    """The window's use per minute up to its latest reading, if the readings before it show one. It's measured
    up to the latest reading, not up to now, so that a gap in the readings doesn't hide the burn."""
    periods = (
        [recent_readings(readings, len(readings) - 1, minutes) for minutes in BURN_SPANS_MINUTES] if readings else []
    )
    rates = [rate(period) for period in periods if period]
    return max(rates) if rates else None


def rate(period: list[Reading]) -> float:
    """The use per minute from the first of the readings to the last. A whole-percent reading and an agent's fraction
    of the window can differ by a rounding error, such as 57 and 56.99999999999999, so it's never below 0."""
    return max(0.0, (period[-1].used - period[0].used) / ((period[-1].time - period[0].time) / 60))


def recent_readings(readings: list[Reading], last: int, minutes: float) -> list[Reading] | None:
    """The readings of reading `last`'s window in the `minutes` up to it, if they're far enough apart to show a
    rate. A reading of another window among them, such as a stale one, is left out."""
    end = readings[last]
    period = []
    for index in range(last, -1, -1):
        if end.time - readings[index].time > minutes * 60:
            break
        if readings[index].resets_at == end.resets_at:
            period.append(readings[index])
    period.reverse()
    if len(period) < 2 or period[-1].time - period[0].time < 120:
        return None
    return period


@dataclass(frozen=True)
class Load:
    """What runs on an account, and how it behaves."""

    agents: int
    reaction: float  # minutes an agent takes to stop after a notice
    interval: float  # minutes until the next decision
    # When each agent that ran during the readings started and stopped (None while it runs), in seconds since the
    # epoch.
    runs: tuple[tuple[float, float | None], ...]
    reserve: float = 0.0  # percent of each window that the loop leaves alone
    measured_since_last_start: bool = True  # whether a reading shows what the agents started last use


def per_agent_rate(readings: list[Reading], load: Load) -> float | None:
    """One agent's use of the window per minute, measured at the latest reading or, if too few agents ran in the
    readings up to it, such as after a pause, at the latest reading before it where enough did."""
    for last in range(len(readings) - 1, -1, -1):
        if (each := per_agent_rate_at(readings, last, load)) is not None:
            return each
    return None


def per_agent_rate_at(readings: list[Reading], last: int, load: Load) -> float | None:
    """One agent's use per minute in the readings up to reading `last`: each span's rate shared among the agents that
    ran during it, the largest of these. An agent that started after a span's readings used nothing that they show,
    and one that stopped during them used some of what they show, so the agents that run now would give the wrong
    share. When no span shows a rise, the use is below what one READING_STEP over a span shows, and the smallest of
    those bounds counts: a new agent's use doesn't show in whole percents for minutes, but a slow agent's never
    does."""
    rates, bounds = [], []
    for minutes in BURN_SPANS_MINUTES:
        period = recent_readings(readings, last, minutes)
        if period is None:
            continue
        start, end = period[0].time, period[-1].time
        agents = agents_during(load.runs, start, end)
        if agents < MIN_AGENTS_IN_SPAN:
            continue
        if used := rate(period):
            rates.append(used / agents)
        else:
            bounds.append(READING_STEP / ((end - start) / 60) / agents)
    if rates:
        return max(rates)
    return min(bounds) if bounds else None


def agents_during(runs: tuple[tuple[float, float | None], ...], start: float, end: float) -> float:
    """How many agents ran between two times, on average."""
    overlaps = [max(0.0, min(end, stop or end) - max(start, began)) for began, stop in runs]
    return sum(overlaps) / (end - start)


def decide_window(readings: list[Reading], now: float, load: Load) -> Decision:
    """What to do about one usage window.

    An agent keeps using the window until it stops, so the agents that may keep running are the ones that can
    still be told at the next decision and stop in time.
    """
    latest = readings[-1] if readings else None
    fresh_window = latest is None or latest.resets_at <= now
    measured = None if fresh_window else burn_rate(readings)
    measured_per_agent = None if fresh_window else per_agent_rate(readings, load)
    per_agent = DEFAULT_AGENT_BURN if measured_per_agent is None else measured_per_agent
    # Agents that started since the readings don't show in them yet, so each running agent counts at the rate that
    # the agents in the readings used.
    burn = max(measured or 0.0, per_agent * load.agents)
    # A reading that isn't fresh is projected forward, so that missing data slows the loop down instead of hiding.
    used = 0.0 if fresh_window else latest.used + burn * (now - latest.time) / 60
    limit = 100.0 - FLOOR - READING_STEP - load.reserve
    spare = limit - used - burn * load.reaction
    if spare <= 0:
        return Decision("pause", 0, load.agents, used, burn)
    keep = math.floor(spare / (per_agent * load.interval))
    if keep < load.agents:
        return Decision("wind down", 0, load.agents - keep, used, burn)
    # More agents start only once a reading shows what the last ones use, and at most double the running count, or
    # two.
    stale = not fresh_window and now - latest.time > STALE_MINUTES * 60
    if stale or not load.measured_since_last_start:
        return Decision("hold", 0, 0, used, burn)
    horizon = MIN_RUN_MINUTES + load.reaction
    allowed = math.floor((limit - used - burn * horizon) / (per_agent * horizon))
    room = max(0, min(allowed, max(2, load.agents)))
    return Decision("start" if room else "hold", room, 0, used, burn)


def decide(windows: dict[str, list[Reading]], now: float, load: Load) -> Decision:
    """The strictest decision over an account's windows, such as its 5-hour and weekly ones."""
    decisions = [decide_window(readings, now, load) for readings in windows.values()] or [decide_window([], now, load)]
    strictest = max(decisions, key=lambda decision: ORDER.index(decision.action))
    room = min(decision.room for decision in decisions)
    wind_down = max(decision.wind_down for decision in decisions)
    return Decision(strictest.action, room, wind_down, strictest.used, strictest.burn)
