"""The checks that scripts/watch runs every minute, each returning the events that should wake the orchestrator."""

import json
import subprocess
import time
from pathlib import Path

import contexts
import github
import meter
import metering
import refreshing
import thresholds
import usage

ROOT = usage.ROOT
STATE = usage.LOOP / "watch.json"
PID = usage.LOOP / "watch.pid"
CONTEXTS = usage.LOOP / "contexts.json"
SENDER_PID = usage.LOOP / "send-clear.pid"
# The orchestrator holds off a refresh while the maintainer wrote to it within this many minutes.
ACTIVE_MINUTES = 5
# A refresh that hasn't happened this long after the request has failed, unless the sender says what it waits for.
REFRESH_SECONDS = 180
STALLED_MINUTES = 30
# The orchestrator's prompt cache lasts an hour, so the watcher wakes it before then.
WARM_WAKE_SECONDS = 50 * 60
# Without a limit, gh lists only 30 pull requests, and the ones it leaves out would count as merged.
PULL_REQUEST_LIMIT = 1000
CLAUDE_WRITE_TO_READ = 31.0  # zzz's measurement on a Claude subscription
# On the API, a Haiku request whose prompt is over this many tokens costs this many times more.
PRICE_STEPS = {"claude-haiku": (100_000, 5.0)}

HAND_OFF = (
    "Hand off soon: your context has grown past the point where a fresh builder usually costs less. Keep going "
    "until you reach a point where a successor wouldn't have to reread what you've loaded for the step you're on. "
    "If the rest of your issue needs little more reading, finish it instead. Otherwise, hand off to a successor as "
    "the building skill describes."
)
HAND_OFF_NOW = (
    "Hand off now: your context is close to its limit. Do the hand-off as soon as the step you're on is done."
)
REFRESH = (
    "Refresh soon: your context has grown past the point where a fresh session usually costs less. Keep going "
    "until you reach a point where the next session wouldn't have to reread what you've loaded. Then run "
    "scripts/refresh and do what it says."
)
REFRESH_NOW = (
    "Refresh now: your context is close to its limit, and the agents are winding down. As soon as the step you're "
    "on is done, run scripts/refresh and do what it says."
)
GITHUB_FAILED = (
    "GitHub failed for the watcher: {error}. It keeps trying every minute, and reports pull requests again once "
    "GitHub answers."
)
REFRESH_FAILED = (
    "The refresh failed twice. Send the maintainer a notification that asks them to type /clear in this session and "
    'press Enter, then type "Continue the loop." and press Enter. Carry on with the loop until then.'
)


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def context_limits(model: str | None, window: int | None) -> thresholds.Context:
    window = window or 1_000_000
    boundary, multiplier = next(
        (step for prefix, step in PRICE_STEPS.items() if model and model.startswith(prefix)), (None, 1.0)
    )
    # Claude's automatic compaction is off, so the first point outside the loop's control is the window itself.
    return thresholds.Context(window, window, boundary, multiplier, CLAUDE_WRITE_TO_READ)


def sessions() -> list[tuple[str, str, str | None, Path, int | None]]:
    """Each watched session that has a transcript: its notice ID, its agent type, its account, its transcript and its
    context window, if known."""
    orchestrator = usage.orchestrator()
    watched = []
    for agent in usage.running_agents():
        if transcript := usage.transcript(agent["account"], agent["session"]):
            watched.append((agent["id"], agent["name"], agent["account"], transcript, None))
    if orchestrator.get("transcript"):
        transcript = Path(orchestrator["transcript"])
        account, window = orchestrator.get("account"), orchestrator.get("window")
        watched.append((usage.ORCHESTRATOR_ID, "orchestrator", account, transcript, window))
    return watched


def warm_wake_at(started: float) -> float:
    """When the watcher wakes the orchestrator to keep its prompt cache warm: counted from the orchestrator's last
    turn, because a report can wake it while the watcher runs on, or from the watcher's start before any turn."""
    transcript = usage.orchestrator().get("transcript")
    ended = refreshing.last_turn_end(Path(transcript)) if transcript else None
    # The turn that started this watcher hasn't ended yet, so a turn that ended earlier counts from the start.
    return max(ended or started, started) + WARM_WAKE_SECONDS


def meter_changes(state: dict, decisions: dict[str, meter.Decision]) -> list[str]:
    """The accounts whose meter decision changed since the last check. The decisions are kept in the watcher's
    state, so that a change while no watcher ran is reported by the next one."""
    before = state.get("actions")
    state["actions"] = {name: decision.action for name, decision in decisions.items()}
    if before is None:
        return []
    changed = [name for name, decision in decisions.items() if before.get(name) != decision.action]
    return [f"Usage: {name} now says {decisions[name].action}." for name in changed]


def check_contexts() -> list[str]:
    """Tells each session that's past its refresh point to hand off or refresh, and stops everything safely when
    one nears its limit."""
    known = contexts.load(CONTEXTS)
    events = []
    for agent_id, agent_type, account, transcript, window in sessions():
        context = contexts.update(known.get(str(transcript), contexts.Context()), transcript)
        known[str(transcript)] = context
        if context.start is None:
            continue
        # Claude Code reports an agent's window only at the end of a turn, and an account's plan can hold a model to
        # a smaller window than another account's.
        window = window or usage.context_window(account, context.model)
        limits = thresholds.compute(context_limits(context.model, window), context.start, context.growth())
        near_limit = notify_about_context(agent_id, agent_type, context.current, limits)
        if near_limit and not context.reported_limit:
            context.reported_limit = True
            events += near_limit
    contexts.save(CONTEXTS, {path: c for path, c in known.items() if Path(path).exists()})
    return events


def notify_about_context(agent_id: str, agent_type: str, current: int, limits: thresholds.Thresholds) -> list[str]:
    is_orchestrator = agent_id == usage.ORCHESTRATOR_ID
    if current >= limits.emergency:
        usage.notify(agent_id, "context-now", REFRESH_NOW if is_orchestrator else HAND_OFF_NOW)
        if not is_orchestrator:
            # The agent's report after its hand-off wakes the orchestrator.
            return []
        for agent in usage.running_agents():
            usage.notify(agent["id"], "wind-down", metering.WIND_DOWN)
        return [f"{agent_type}'s context is at {current:,} tokens, near its limit. Everything is winding down."]
    active = is_orchestrator and usage.minutes_since_maintainer_wrote() < ACTIVE_MINUTES
    if current >= (limits.while_active if active else limits.refresh):
        usage.notify(agent_id, "context", REFRESH if is_orchestrator else HAND_OFF)
    return []


def check_refresh(state: dict) -> list[str]:
    """Retries a refresh once when it hasn't happened in time, and has the orchestrator tell the maintainer when the
    retry fails too. Missing the refresh point only makes each turn cost more, so the loop carries on; the emergency
    point near the context's limit still winds everything down.
    A sender that still runs is waiting for a reason, and a request that a tool call cancelled is the Stop hook's
    to handle."""
    saved = refreshing.load_request()
    if saved is None or refreshing.is_cancelled(saved) or is_sender_running():
        return []
    if time.time() - saved["time"] < REFRESH_SECONDS:
        return []
    retried = state.get("refresh_retried", {})
    if retried.get("request") != saved["time"]:
        state["refresh_retried"] = {"request": saved["time"], "time": time.time()}
        subprocess.Popen([str(ROOT / "scripts" / "send-clear")], stdin=subprocess.DEVNULL, start_new_session=True)
        return []
    if time.time() - retried["time"] < REFRESH_SECONDS or retried.get("reported"):
        return []
    retried["reported"] = True
    # Dropped, so that the orchestrator's next tool call doesn't cancel it and ask for another try.
    refreshing.REQUEST.unlink(missing_ok=True)
    refreshing.FAILED.touch()
    waiting = refreshing.WAITING.read_text().strip() if refreshing.WAITING.exists() else ""
    return [REFRESH_FAILED + (f" The sender's reason, to pass on: {waiting}" if waiting else "")]


def is_sender_running() -> bool:
    """Whether scripts/send-clear still runs."""
    pid = usage.pid_in(SENDER_PID)
    return pid is not None and usage.is_running(pid, "send-clear")


def check_pull_requests(state: dict) -> list[str]:
    try:
        listed = github.gh_json("pr", "list", "--limit", str(PULL_REQUEST_LIMIT), "--json", "number,statusCheckRollup")
    except SystemExit as failure:
        # Reported once, because GitHub can fail for a while, such as in an outage. The next check that works
        # compares with the last list that came.
        if state.get("github_failing"):
            return []
        state["github_failing"] = True
        return [GITHUB_FAILED.format(error=failure)]
    state.pop("github_failing", None)
    current = {str(pr["number"]): checks_state(pr) for pr in listed or []}
    before = state.get("pull_requests", current)
    state["pull_requests"] = current
    events = [f"#{number} merged or closed." for number in before if number not in current]
    events += [f"#{number} opened." for number in current if number not in before]
    events += [f"#{number}'s checks failed." for number, s in current.items() if s == "failing" != before.get(number)]
    return events


def checks_state(pr: dict) -> str:
    states = {check.get("conclusion") or check.get("status") for check in pr["statusCheckRollup"]}
    return "failing" if "FAILURE" in states else "passing" if states <= {"SUCCESS", "SKIPPED"} else "running"


def check_stalled(state: dict) -> list[str]:
    reported = set(state.get("stalled", []))
    events = []
    for agent in usage.running_agents():
        transcript = usage.transcript(agent["account"], agent["session"])
        idle = time.time() - (transcript.stat().st_mtime if transcript else agent["started"])
        if idle > STALLED_MINUTES * 60 and agent["id"] not in reported:
            reported.add(agent["id"])
            events.append(f"{agent['name']} ({agent['id']}) has done nothing for {idle / 60:.0f} minutes.")
    state["stalled"] = sorted(reported)
    return events
