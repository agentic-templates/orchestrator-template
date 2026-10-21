"""The checks that scripts/watch runs every minute, each returning the events that should wake the orchestrator."""

import json
import subprocess
import tempfile
import time
from pathlib import Path

import contexts
import github
import metering
import thresholds
import usage

ROOT = usage.ROOT
STATE = usage.LOOP / "watch.json"
PID = usage.LOOP / "watch.pid"
CONTEXTS = usage.LOOP / "contexts.json"
REFRESH_REQUEST = usage.LOOP / "refresh-request"
REFRESH_WAITING = usage.LOOP / "refresh-waiting"
# The orchestrator holds off a refresh while the maintainer wrote to it within this many minutes.
ACTIVE_MINUTES = 5
# A refresh that hasn't happened this long after the request has failed, unless the sender says what it waits for.
REFRESH_SECONDS = 180
STALLED_MINUTES = 30
CLAUDE_WRITE_TO_READ = 31.0  # zzz's measurement on a Claude subscription
# On the API, a Haiku request whose prompt is over this many tokens costs this many times more.
PRICE_STEPS = {"claude-haiku": (100_000, 5.0)}

HAND_OFF = (
    "Hand off soon: your context has grown past the point where a fresh builder usually costs less. Keep going "
    "until you reach a point where a successor wouldn't have to reread what you've loaded for the step you're on. "
    "If the rest of your issue needs little more reading, finish it instead. Then push your branch, write "
    ".loop/handoff/issue-<number>.md as the building skill describes, and end with your report."
)
HAND_OFF_NOW = (
    "Hand off now: your context is close to its limit. Do the hand-off as soon as the step you're on is done."
)
REFRESH = (
    "Refresh soon: your context has grown past the point where a fresh session usually costs less. Keep going "
    "until you reach a point where the next session wouldn't have to reread what you've loaded. Then rewrite "
    ".loop/state.md and run scripts/refresh as the last step of your turn."
)
REFRESH_NOW = (
    "Refresh now: your context is close to its limit, and the agents are winding down. Rewrite .loop/state.md and "
    "run scripts/refresh as soon as the step you're on is done."
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


def sessions() -> list[tuple[str, str, Path, int | None]]:
    """Each watched session: its notice ID, its agent type, its transcript and its context window, if known."""
    orchestrator = usage.orchestrator()
    watched = [(agent["id"], agent["name"], Path(agent["transcript"]), None) for agent in usage.running_agents()]
    if orchestrator.get("transcript"):
        watched.append(
            (usage.ORCHESTRATOR_ID, "orchestrator", Path(orchestrator["transcript"]), orchestrator.get("window"))
        )
    return watched


def check_contexts() -> list[str]:
    """Tells each session that's past its refresh point to hand off or refresh, and stops everything safely when
    one nears its limit."""
    known = contexts.load(CONTEXTS)
    events = []
    for agent_id, agent_type, transcript, window in sessions():
        context = contexts.update(known.get(str(transcript), contexts.Context()), transcript)
        known[str(transcript)] = context
        if context.start is None:
            continue
        limits = thresholds.compute(context_limits(context.model, window), context.start, context.growth())
        events += notify_about_context(agent_id, agent_type, context.current, limits)
    contexts.save(CONTEXTS, {path: c for path, c in known.items() if Path(path).exists()})
    return events


def notify_about_context(agent_id: str, agent_type: str, current: int, limits: thresholds.Thresholds) -> list[str]:
    is_orchestrator = agent_id == usage.ORCHESTRATOR_ID
    if current >= limits.emergency:
        usage.notify(agent_id, "context-now", REFRESH_NOW if is_orchestrator else HAND_OFF_NOW)
        if is_orchestrator:
            for agent in usage.running_agents():
                usage.notify(agent["id"], "wind-down", metering.WIND_DOWN)
        return [f"{agent_type}'s context is at {current:,} tokens, near its limit. Everything is winding down."]
    active = is_orchestrator and usage.minutes_since_maintainer_wrote() < ACTIVE_MINUTES
    if current >= (limits.while_active if active else limits.refresh):
        usage.notify(agent_id, "context", REFRESH if is_orchestrator else HAND_OFF)
    return []


def check_refresh(state: dict) -> list[str]:
    """Retries a refresh once when it hasn't happened in time, and reports it when the retry fails too."""
    if not REFRESH_REQUEST.exists() or REFRESH_WAITING.exists():
        state.pop("refresh_retried", None)
        return []
    if time.time() - REFRESH_REQUEST.stat().st_mtime < REFRESH_SECONDS:
        return []
    if not state.get("refresh_retried"):
        state["refresh_retried"] = time.time()
        subprocess.Popen([str(ROOT / "scripts" / "send-clear")], start_new_session=True)
        return []
    if time.time() - state["refresh_retried"] > REFRESH_SECONDS:
        return ["The refresh failed twice. Clear the orchestrator's session by hand, or check scripts/send-clear."]
    return []


def check_pull_requests(state: dict) -> list[str]:
    fields = "number,title,statusCheckRollup"
    current = {str(pr["number"]): checks_state(pr) for pr in github.gh_json("pr", "list", "--json", fields) or []}
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
        transcript = Path(agent["transcript"])
        idle = time.time() - transcript.stat().st_mtime if transcript.exists() else 0
        if idle > STALLED_MINUTES * 60 and agent["id"] not in reported:
            reported.add(agent["id"])
            events.append(f"{agent['name']} ({agent['id']}) has done nothing for {idle / 60:.0f} minutes.")
    state["stalled"] = sorted(reported)
    return events


def check_models(state: dict) -> list[str]:
    """Opens a `loop` issue when an agent type starts running a model it hasn't run before, such as when an alias
    like "opus" moves to a newer model."""
    seen = state.setdefault("models", {})
    known = contexts.load(CONTEXTS)
    events = []
    for _, agent_type, transcript, _ in sessions():
        model = known.get(str(transcript), contexts.Context()).model
        if not model or seen.get(agent_type) == model:
            continue
        if agent_type in seen:
            events.append(open_model_issue(agent_type, seen[agent_type], model))
        seen[agent_type] = model
    return events


def open_model_issue(agent_type: str, old: str, new: str) -> str:
    body = (
        f"## What should change\n\n`{agent_type}` keeps its model and effort only if its model tests show {new} does "
        f"as well as {old} at the same or lower cost.\n\n## Why\n\n`{agent_type}` now runs {new}, where it ran "
        f"{old}, so the results that chose its model no longer apply.\n\n## Done when\n\n- A comment gives the "
        "model tests' results for the new model, and a recommendation.\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as file:
        file.write(body)
    title = f"Rerun the model tests of {agent_type}, which now runs {new}"
    command = [str(ROOT / "scripts" / "new-issue"), "--title", title, "--body-file", file.name]
    subprocess.run([*command, "--label", "loop", "--label", "research"], capture_output=True, check=False)
    return f"{agent_type} now runs {new}, where it ran {old}. A loop issue asks to rerun its model tests."
