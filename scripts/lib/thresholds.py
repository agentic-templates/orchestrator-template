"""When a session should move to a fresh context: at its best point for cost, later while the maintainer is
typing to it, and in an emergency before anything happens that the loop doesn't control.

A session's cost per turn grows with its context, because each turn reads the whole context from the cache.
A fresh session costs its starting context B, written to the cache once. The best point to move is
T* = B + sqrt(2 * r * B * g), where g is the context a turn adds and r is how many times a cache write costs a
cache read. This is the formula that an earlier project, zzz, measured.
"""

import itertools
import math
from dataclasses import dataclass

# The loop moves to a fresh context at its best point, waits up to this many times longer while the maintainer
# is typing to the session, and stops everything safely at this share of the first point it doesn't control.
ACTIVE_FACTOR = 1.5
EMERGENCY_SHARE = 0.8


@dataclass(frozen=True)
class Context:
    """What a session's model allows, in tokens."""

    window: int
    uncontrolled: int  # where something happens that the loop doesn't control: compaction or a hard stop
    price_boundary: int | None = None  # where every token of a request starts to cost more
    price_multiplier: float = 1.0
    write_to_read: float = 31.0  # r: what a cache write costs, in cache reads


@dataclass(frozen=True)
class Thresholds:
    refresh: int  # move to a fresh context at the next stopping point
    while_active: int  # the same, while the maintainer is typing to the session
    emergency: int  # wind everything down and stop safely


def best_point(start: int, growth: int, write_to_read: float) -> int:
    return round(start + math.sqrt(2 * write_to_read * start * growth))


def compute(context: Context, start: int, growth: int) -> Thresholds:
    """The thresholds for a session that started with `start` tokens and adds `growth` tokens a turn.

    Above a price boundary, every turn costs `price_multiplier` times more, while a fresh session starts below it
    at the normal price. So a session that would otherwise run past the boundary moves just before it, unless
    even a fresh session starts too close to the boundary to stay under it.
    """
    emergency = math.floor(context.uncontrolled * EMERGENCY_SHARE)
    refresh = best_point(start, growth, context.write_to_read)
    boundary = context.price_boundary
    if boundary and context.price_multiplier > 1 and start + 2 * growth < boundary < refresh:
        refresh = boundary - growth
    refresh = min(refresh, emergency - 2 * growth)
    while_active = min(round(refresh * ACTIVE_FACTOR), emergency - growth)
    thresholds = Thresholds(refresh, while_active, emergency)
    check_order(thresholds, context, start)
    return thresholds


def check_order(thresholds: Thresholds, context: Context, start: int) -> None:
    order = [start, thresholds.refresh, thresholds.while_active, thresholds.emergency, context.uncontrolled]
    if any(earlier >= later for earlier, later in itertools.pairwise(order)):
        raise ValueError(
            f"The context thresholds don't fit this model's window: start {start}, refresh {thresholds.refresh}, "
            f"while active {thresholds.while_active}, emergency {thresholds.emergency}, and something outside the "
            f"loop's control at {context.uncontrolled}. Raise the session's context window."
        )
