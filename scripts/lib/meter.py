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
# The burn rate is the larger of the rates over these spans of recent readings, so that a rise shows quickly
# and a fall only once it lasts.
BURN_SPANS_MINUTES = (5.0, 20.0)


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


def burn_rate(readings: list[Reading], now: float) -> float | None:
    """The window's use per minute in its current period, if the recent readings show it."""
    rates = [rate for span in BURN_SPANS_MINUTES if (rate := rate_over(readings, now, span)) is not None]
    return max(rates) if rates else None


def rate_over(readings: list[Reading], now: float, minutes: float) -> float | None:
    if not readings:
        return None
    period = [r for r in readings if r.resets_at == readings[-1].resets_at and now - r.time <= minutes * 60]
    if len(period) < 2 or period[-1].time - period[0].time < 120:
        return None
    return max(0.0, (period[-1].used - period[0].used) / ((period[-1].time - period[0].time) / 60))


@dataclass(frozen=True)
class Load:
    """What runs on an account, and how it behaves."""

    agents: int
    reaction: float  # minutes an agent takes to stop after a notice
    interval: float  # minutes until the next decision
    reserve: float = 0.0  # percent kept back, such as for the orchestrator
    new_agent_burn: float | None = None  # what the next agent is expected to use, if known for its model


def decide_window(readings: list[Reading], now: float, load: Load) -> Decision:
    """What to do about one usage window.

    An agent keeps using the window until it stops, so the agents that may keep running are the ones that can
    still be told at the next decision and stop in time.
    """
    latest = readings[-1] if readings else None
    fresh_window = latest is None or latest.resets_at <= now
    measured = None if fresh_window else burn_rate(readings, now)
    burn = measured if measured is not None else DEFAULT_AGENT_BURN * load.agents
    per_agent = burn / load.agents if load.agents and burn > 0 else DEFAULT_AGENT_BURN
    # A reading that isn't fresh is projected forward, so that missing data slows the loop down instead of hiding.
    used = 0.0 if fresh_window else latest.used + burn * (now - latest.time) / 60
    limit = 100.0 - FLOOR - load.reserve
    spare = limit - used - burn * load.reaction
    if spare <= 0:
        return Decision("pause", 0, load.agents, used, burn)
    keep = math.floor(spare / (per_agent * load.interval))
    if keep < load.agents:
        return Decision("wind down", 0, load.agents - keep, used, burn)
    # Each new agent must be able to run until the next decision and still stop in time. Starts at most double
    # the agents, so that the burn is measured before more start.
    horizon = load.reaction + load.interval
    allowed = math.floor((limit - used - burn * horizon) / ((load.new_agent_burn or per_agent) * horizon))
    room = max(0, min(allowed, max(2, load.agents)))
    return Decision("start" if room else "hold", room, 0, used, burn)


def decide(windows: dict[str, list[Reading]], now: float, load: Load) -> Decision:
    """The strictest decision over an account's windows, such as its 5-hour and weekly ones."""
    decisions = [decide_window(readings, now, load) for readings in windows.values()] or [decide_window([], now, load)]
    strictest = max(decisions, key=lambda decision: ORDER.index(decision.action))
    room = min(decision.room for decision in decisions)
    wind_down = max(decision.wind_down for decision in decisions)
    return Decision(strictest.action, room, wind_down, strictest.used, strictest.burn)
