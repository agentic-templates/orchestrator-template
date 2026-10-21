"""The loop's accounts, their usage readings and the agents running on them, all kept in `.loop/`."""

import json
import os
import select
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from meter import Reading


def main_checkout() -> Path:
    """The repository's main checkout, which holds the loop's state, even for a script that runs in a worktree."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(common).parent


ROOT = main_checkout()
LOOP = ROOT / ".loop"
ACCOUNTS = LOOP / "accounts.json"
USAGE = LOOP / "usage"
AGENTS = LOOP / "agents"
NOTICES = LOOP / "notices"
# The orchestrator's notices go under this name, because its session isn't a subagent with an ID.
ORCHESTRATOR_ID = "orchestrator"
ORCHESTRATOR = LOOP / "orchestrator.json"
MAINTAINER = LOOP / "maintainer.json"
# Readings older than this tell nothing about the current burn, so the file keeps only these.
KEEP_SECONDS = 8 * 3600


@dataclass(frozen=True)
class Account:
    name: str
    provider: str  # "claude" or "codex"
    home: str | None  # the CLAUDE_CONFIG_DIR or CODEX_HOME, or None for the default


def accounts() -> list[Account]:
    """The accounts in .loop/accounts.json, or this machine's default Claude account without that file."""
    if not ACCOUNTS.exists():
        return [Account("claude", "claude", None)]
    return [Account(item["name"], item["provider"], item.get("home")) for item in json.loads(ACCOUNTS.read_text())]


def account_of(environment: dict[str, str], provider: str = "claude") -> str:
    """The name of the account that a process with this environment uses."""
    variable = "CLAUDE_CONFIG_DIR" if provider == "claude" else "CODEX_HOME"
    home = environment.get(variable)
    for account in accounts():
        if account.provider == provider and expand(account.home) == expand(home):
            return account.name
    return provider if home is None else Path(home).name


def expand(home: str | None) -> str | None:
    return None if home is None else os.path.realpath(os.path.expanduser(home))


def environment_for(account: Account) -> dict[str, str]:
    environment = dict(os.environ)
    variable = "CLAUDE_CONFIG_DIR" if account.provider == "claude" else "CODEX_HOME"
    environment.pop(variable, None)
    if account.home:
        environment[variable] = os.path.expanduser(account.home)
    return environment


def record(account: str, window: str, used: float, resets_at: float) -> None:
    """Saves one reading, unless it only repeats the last one within a minute."""
    USAGE.mkdir(parents=True, exist_ok=True)
    readings = windows(account).get(window, [])
    now = time.time()
    if readings and readings[-1].used == used and now - readings[-1].time < 60:
        return
    with open(USAGE / f"{account}.jsonl", "a") as file:
        file.write(json.dumps({"time": now, "window": window, "used": used, "resets_at": resets_at}) + "\n")


def windows(account: str) -> dict[str, list[Reading]]:
    path = USAGE / f"{account}.jsonl"
    if not path.exists():
        return {}
    cutoff = time.time() - KEEP_SECONDS
    lines = [json.loads(line) for line in path.read_text().splitlines() if line]
    kept = [line for line in lines if line["time"] >= cutoff]
    if len(kept) < len(lines):
        path.write_text("".join(json.dumps(line) + "\n" for line in kept))
    result: dict[str, list[Reading]] = {}
    for line in kept:
        result.setdefault(line["window"], []).append(Reading(line["time"], line["used"], line["resets_at"]))
    return result


def record_claude_event(account: str, event: dict) -> None:
    """Saves the readings in a `rate_limit_event` from Claude Code's stream-json output."""
    for window, values in (event.get("rate_limit_info", event).get("unifiedWindows") or {}).items():
        record(account, window, values["utilization"] * 100, values["resetsAt"])


def record_status_line(account: str, status: dict) -> None:
    """Saves the readings that Claude Code passes to the status line."""
    for window, values in (status.get("rate_limits") or {}).items():
        if values and values.get("used_percentage") is not None:
            record(account, window, values["used_percentage"], values["resets_at"])


def probe(account: Account) -> None:
    """Takes a fresh reading of the account: a one-turn Haiku run for Claude, and a usage request for Codex."""
    if account.provider == "claude":
        output = subprocess.run(
            ["claude", "-p", "--model", "haiku", "--output-format", "stream-json", "--verbose", "Reply with ok."],
            env=environment_for(account),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        ).stdout
        for line in output.splitlines():
            event = json.loads(line) if line.startswith("{") else {}
            if event.get("type") == "rate_limit_event":
                record_claude_event(account.name, event)
    else:
        for window, values in codex_rate_limits(account).items():
            record(account.name, window, values["usedPercent"], values["resetsAt"])


def codex_rate_limits(account: Account) -> dict[str, dict]:
    """Codex's usage windows, read through its app server, which needs no model call."""
    server = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment_for(account),
        text=True,
    )
    try:
        for request in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "meter", "version": "1"}},
            },
            {"jsonrpc": "2.0", "method": "initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"},
        ):
            server.stdin.write(json.dumps(request) + "\n")
        server.stdin.flush()
        return read_rate_limits(server)
    finally:
        server.terminate()
        server.wait(timeout=5)


def read_rate_limits(server: subprocess.Popen) -> dict[str, dict]:
    deadline = time.time() + 30
    while time.time() < deadline and select.select([server.stdout], [], [], 1)[0]:
        line = server.stdout.readline()
        message = json.loads(line) if line.startswith("{") else {}
        if message.get("id") == 2:
            limits = message.get("result", {}).get("rateLimits", message.get("result", {}))
            windows = (limits.get("primary"), limits.get("secondary"))
            return {f"{values['windowDurationMins']}m": values for values in windows if values}
    return {}


def running_agents() -> list[dict]:
    """The agents that scripts/launch started and that still run, oldest first. An agent whose launcher has gone
    without saying so, such as after a crash, is marked as stopped."""
    if not AGENTS.exists():
        return []
    running = []
    for path in AGENTS.glob("*.json"):
        agent = json.loads(path.read_text())
        if "stopped" in agent:
            continue
        if is_running(agent["pid"]):
            running.append(agent)
        else:
            save_agent({**agent, "stopped": time.time()})
    return sorted(running, key=lambda agent: agent["started"])


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def save_agent(agent: dict) -> None:
    AGENTS.mkdir(parents=True, exist_ok=True)
    (AGENTS / f"{agent['id']}.json").write_text(json.dumps(agent))


def notify(agent_id: str, kind: str, message: str) -> None:
    """Leaves a message that the notice hook shows the agent once, after its next batch of tool calls. Each kind
    of notice, such as "wind-down", is shown at most once to an agent."""
    folder = NOTICES / agent_id
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / f"{kind}.told").exists():
        (folder / kind).write_text(message)


def untold_notices(agent_id: str) -> list[str]:
    """The notices left for the agent that it hasn't seen, which this marks as seen."""
    folder = NOTICES / agent_id
    if not folder.is_dir():
        return []
    messages = []
    for notice in sorted(path for path in folder.iterdir() if path.suffix != ".told"):
        if not notice.with_suffix(".told").exists():
            messages.append(notice.read_text())
            notice.with_suffix(".told").touch()
    return messages


def orchestrator() -> dict:
    """The orchestrator's account and model, which its session records."""
    return json.loads(ORCHESTRATOR.read_text()) if ORCHESTRATOR.exists() else {}


def minutes_since_maintainer_wrote() -> float:
    if not MAINTAINER.exists():
        return float("inf")
    return (time.time() - json.loads(MAINTAINER.read_text())["last_message"]) / 60


def orchestrator_account() -> str | None:
    return orchestrator().get("account")


def agent_config(agent_type: str) -> str:
    """The model and effort that an agent type runs with, such as "opus/medium", from its definition."""
    path = ROOT / ".claude" / "agents" / f"{agent_type}.md"
    fields = {}
    if path.exists():
        for line in path.read_text().split("---")[1].splitlines():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return f"{fields.get('model', 'default')}/{fields.get('effort', 'default')}"
