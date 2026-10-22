"""The meter's decisions for every account, from their readings and the agents that run on them."""

import json
import math
import shutil
import time

import measurements
import meter
import usage

CHECK_SECONDS = 60
# A reading this old is replaced before the decision, more often for an account that agents are using. An idle
# account's reading is replaced once it's too old for an agent to start on.
STALE_BUSY_SECONDS = 5 * 60
STALE_IDLE_SECONDS = meter.STALE_MINUTES * 60
# The providers whose agents scripts/launch can start. Add one only once the launcher can start its agents: for an
# account of any other provider, it would start a Claude agent on the orchestrator's account, and the meter would
# count that agent's usage against the wrong account.
LAUNCHABLE_PROVIDERS = {"claude"}


def reaction_minutes(running: list[dict]) -> float:
    """How long the running agents' models have taken to stop after a notice: the slowest of them."""
    configs = {agent["config"] for agent in running} or {"default"}
    return max(measurements.median_or(f"reaction/{c}", meter.DEFAULT_REACTION_MINUTES) for c in configs)


def latest_reading(account: str) -> float:
    return max((reading.time for window in usage.windows(account).values() for reading in window), default=0.0)


def refresh(account: usage.Account, on_account: list[dict]) -> None:
    """Takes a fresh reading when the last one is old, or when agents started after it, so that the next
    decision sees what they use."""
    latest = latest_reading(account.name)
    started_since = any(agent["started"] > latest for agent in on_account)
    if started_since or time.time() - latest > (STALE_BUSY_SECONDS if on_account else STALE_IDLE_SECONDS):
        usage.probe(account)


WIND_DOWN = (
    "Wind down: the account is close to its usage limit. Finish the step you're on and don't start another. If "
    "you're building an issue, hand off as the building skill describes. Otherwise, end with your report, saying "
    "what's left."
)


def tell_to_wind_down(agents: list[dict], count: int) -> None:
    """Tells the newest agents that haven't been told yet, until `count` of the running agents have been told. The
    decisions run several times a minute, and an agent that was told keeps running until its next stopping point."""
    untold = [agent for agent in agents if "told_to_wind_down" not in agent]
    count -= len(agents) - len(untold)
    for agent in untold[-count:] if count > 0 else []:
        usage.notify(agent["id"], "wind-down", WIND_DOWN)
        agent["told_to_wind_down"] = time.time()
        usage.save_agent(agent)


def clear_old_notices(running: list[dict]) -> None:
    """Removes the notices of agents that stopped, and the folder once it's empty, so that the notice hook
    stops running."""
    if not usage.NOTICES.exists():
        return
    ids = {agent["id"] for agent in running} | {usage.ORCHESTRATOR_ID}
    for folder in usage.NOTICES.iterdir():
        if folder.name not in ids or not any(folder.iterdir()):
            shutil.rmtree(folder)
    if not any(usage.NOTICES.iterdir()):
        usage.NOTICES.rmdir()


def recent_agents(since: float) -> list[dict]:
    """The agents that ran after `since`, stopped or not."""
    if not usage.AGENTS.exists():
        return []
    agents = [json.loads(path.read_text()) for path in usage.AGENTS.glob("*.json")]
    return [agent for agent in agents if agent.get("stopped", since) >= since]


def decisions() -> dict[str, meter.Decision]:
    running = usage.running_agents()
    clear_old_notices(running)
    # As far back as the readings go, because the meter may look back to the last readings with agents in them.
    recent = recent_agents(time.time() - usage.KEEP_SECONDS)
    result = {}
    for account in usage.accounts():
        on_account = [agent for agent in running if agent["account"] == account.name]
        refresh(account, on_account)
        windows = usage.windows(account.name)
        if not windows:
            # Without any reading, the account's use is unknown, so nothing starts on it.
            result[account.name] = meter.Decision("hold", 0, 0, float("nan"), 0.0)
            continue
        latest = latest_reading(account.name)
        load = meter.Load(
            agents=len(on_account),
            reaction=reaction_minutes(on_account),
            interval=CHECK_SECONDS / 60,
            reserve=account.reserve,
            measured_since_last_start=all(agent["started"] < latest for agent in on_account),
            runs=tuple((a["started"], a.get("stopped")) for a in recent if a["account"] == account.name),
        )
        decision = meter.decide(windows, time.time(), load)
        tell_to_wind_down(on_account, decision.wind_down)
        result[account.name] = decision
    return result


def next_account(result: dict[str, meter.Decision], accounts: list[usage.Account]) -> str | None:
    """The account to start the next agent on: the least used one that has room, with the orchestrator's last. Only
    accounts whose provider scripts/launch can start count."""
    orchestrator = usage.orchestrator_account()
    launchable = {account.name for account in accounts if account.provider in LAUNCHABLE_PROVIDERS}
    open_accounts = [name for name, decision in result.items() if decision.room and name in launchable]
    return min(open_accounts, key=lambda name: (name == orchestrator, result[name].used), default=None)


def report_line(name: str, d: meter.Decision) -> str:
    if math.isnan(d.used):
        return f"{name}: {d.action} (no reading yet)"
    return (
        f"{name}: {d.action}"
        + (f", room for {d.room} more" if d.room else "")
        + (f", told {d.wind_down} to wind down" if d.wind_down else "")
        + f" ({d.used:.0f}% used, {d.burn:.2f}% a minute)"
    )


def report(result: dict[str, meter.Decision], accounts: list[usage.Account]) -> str:
    lines = [report_line(name, d) for name, d in result.items()]
    chosen = next_account(result, accounts)
    lines.append(f"Start the next agent on: {chosen}" if chosen else "Start no agent now.")
    return "\n".join(lines)
