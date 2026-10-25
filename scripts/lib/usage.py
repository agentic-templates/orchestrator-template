"""The loop's accounts, their usage readings and the agents running on them, all kept in `.loop/`."""

import contextlib
import ctypes
import json
import os
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
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
# How long an agent may take to stop once told to, before it is killed.
STOP_SECONDS = 30
NOTICES = LOOP / "notices"
HANDOFF = LOOP / "handoff"
# Where a builder writes its hand-off note, inside its own worktree, the only place the sandbox lets it write.
NOTE = Path(".loop") / "handoff.md"
# The orchestrator's notices go under this name, because its session isn't a subagent with an ID.
ORCHESTRATOR_ID = "orchestrator"
ORCHESTRATOR = LOOP / "orchestrator.json"
MAINTAINER = LOOP / "maintainer.json"
# The context window of each model on each account, as Claude Code reported it at the end of an agent's turn.
CONTEXT_WINDOWS = LOOP / "context-windows.json"
# Readings older than this tell nothing about the current burn, so the file keeps only these.
KEEP_SECONDS = 8 * 3600


@dataclass(frozen=True)
class Account:
    name: str
    provider: str  # "claude" or "codex"
    home: str | None  # the CLAUDE_CONFIG_DIR or CODEX_HOME, or None for the default
    reserve: float = 0.0  # percent of each window that the loop leaves alone, such as for the maintainer's own use


def accounts() -> list[Account]:
    """The accounts in .loop/accounts.json, or this machine's default Claude account without that file."""
    if not ACCOUNTS.exists():
        return [Account("claude", "claude", None)]
    return [account_from(item) for item in json.loads(ACCOUNTS.read_text())]


def account_from(item: dict) -> Account:
    reserve = item.get("reserve", 0)
    if not isinstance(reserve, int | float) or not 0 <= reserve < 100:
        raise SystemExit(
            f".loop/accounts.json gives {item['name']} a reserve of {reserve!r}. A reserve is the percent of each "
            "window that the loop leaves alone: a number from 0 up to, but not including, 100."
        )
    return Account(item["name"], item["provider"], item.get("home"), float(reserve))


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


def command_for(account: Account, command: list[str]) -> list[str]:
    """The command, run on the account whatever environment it starts in, such as a tmux server's, which can come
    from a shell on another account."""
    variable = "CLAUDE_CONFIG_DIR" if account.provider == "claude" else "CODEX_HOME"
    home = [f"{variable}={os.path.expanduser(account.home)}"] if account.home else []
    return ["env", "-u", variable, *home, *command]


def environment_for(account: Account) -> dict[str, str]:
    environment = dict(os.environ)
    variable = "CLAUDE_CONFIG_DIR" if account.provider == "claude" else "CODEX_HOME"
    environment.pop(variable, None)
    if account.home:
        environment[variable] = os.path.expanduser(account.home)
    return environment


def write_atomically(path: Path, text: str) -> None:
    """Replaces the file in one step, because other processes read it while this one writes, and a half-written
    file would crash them."""
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
        file.write(text)
    os.replace(file.name, path)


def json_lines(lines: list[str] | list[bytes]) -> list[dict]:
    """The lines that hold JSON objects, without one that another process is still writing, or one cut where the
    reading started."""
    parsed = []
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
            value = json.loads(line)
            if isinstance(value, dict):
                parsed.append(value)
    return parsed


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
    lines = json_lines(path.read_text().splitlines())
    kept = [line for line in lines if line["time"] >= cutoff]
    if len(kept) < len(lines):
        write_atomically(path, "".join(json.dumps(line) + "\n" for line in kept))
    result: dict[str, list[Reading]] = {}
    for line in kept:
        result.setdefault(line["window"], []).append(Reading(line["time"], line["used"], line["resets_at"]))
    return result


def record_claude_event(account: str, event: dict) -> None:
    """Saves the readings in a `rate_limit_event` from Claude Code's stream-json output."""
    for window, values in (event.get("rate_limit_info", event).get("unifiedWindows") or {}).items():
        record(account, window, values["utilization"] * 100, values["resetsAt"])


def record_context_windows(account: str, model_usage: dict) -> None:
    """Saves the context window of each model in a `result` event's `modelUsage`. The window can depend on the
    account's plan as well as the model, so it's kept for each account."""
    known = json.loads(CONTEXT_WINDOWS.read_text()) if CONTEXT_WINDOWS.exists() else {}
    reported = {model: values["contextWindow"] for model, values in model_usage.items() if values.get("contextWindow")}
    windows = known.get(account, {})
    if reported and (windows | reported) != windows:
        write_atomically(CONTEXT_WINDOWS, json.dumps(known | {account: windows | reported}))


def context_window(account: str, model: str | None) -> int | None:
    """The model's context window on the account, once an agent's turn on it there has ended."""
    known = json.loads(CONTEXT_WINDOWS.read_text()) if CONTEXT_WINDOWS.exists() else {}
    return known.get(account, {}).get(model)


def record_status_line(account: str, status: dict) -> None:
    """Saves the readings that Claude Code passes to the status line."""
    for window, values in (status.get("rate_limits") or {}).items():
        if values and values.get("used_percentage") is not None:
            record(account, window, values["used_percentage"], values["resets_at"])


# The probe runs outside the project and without hooks. In the project, it would start as the orchestrator, and
# the orchestrator's Stop hook would keep it going until it started scripts/watch.
PROBE = [
    "claude", "-p", "--model", "haiku", "--setting-sources", "user", "--settings", '{"disableAllHooks": true}',
    "--tools", "", "--no-session-persistence", "--output-format", "stream-json", "--verbose", "Reply with ok.",
]  # fmt: skip


def probe(account: Account) -> None:
    """Takes a fresh reading of the account: a one-turn Haiku run for Claude, and a usage request for Codex."""
    if account.provider == "claude":
        try:
            output = subprocess.run(
                PROBE,
                cwd=tempfile.gettempdir(),
                env=environment_for(account),
                # claude -p adds whatever comes on its input to the prompt, and waits a few seconds for it to start.
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            ).stdout
        except subprocess.TimeoutExpired:
            # The meter projects the last reading forward instead, which slows the loop down until a probe works.
            return
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
    """The agents that scripts/launch started and that still run, oldest first, including an agent whose launcher has
    gone without saying so, such as after a crash, until end_orphans ends it. One whose launcher and session have
    both gone is marked as stopped."""
    if not AGENTS.exists():
        return []
    running = []
    for agent in agent_records():
        if "stopped" in agent:
            continue
        if is_running(agent["pid"], "launch") or is_orphan(agent):
            running.append(agent)
        else:
            save_agent({**agent, "stopped": time.time()})
    return sorted(running, key=lambda agent: agent["started"])


def agent_records() -> list[dict]:
    """The record of every agent that scripts/launch started."""
    return [record for path in AGENTS.glob("*.json") if (record := agent_record(path.stem))]


def agent_record(agent_id: str) -> dict | None:
    # A launch that fails deletes its record, which can happen while another script lists or reads it.
    try:
        return json.loads((AGENTS / f"{agent_id}.json").read_text())
    except FileNotFoundError:
        return None


def is_orphan(agent: dict) -> bool:
    """Whether the agent's session still runs after its launcher has gone."""
    return bool(agent.get("child")) and is_running(agent["child"], agent["id"])


def end_orphans() -> None:
    """Ends each agent whose launcher has gone, and marks it as stopped. Without its launcher, nothing reads the
    agent's session or saves its work, so it's ended before its work counts as left behind. This can take up to
    STOP_SECONDS for each."""
    for agent in running_agents():
        # Checked again for each agent, because ending the one before can take STOP_SECONDS, and a launcher can
        # finish or fail meanwhile.
        if is_orphan(agent) and not is_running(agent["pid"], "launch"):
            end(agent["child"], agent["id"])
            save_agent({**agent, "stopped": time.time()})


def end(pid: int, name: str) -> None:
    """Stops the process if its command line has `name`, and kills it if it hasn't stopped within STOP_SECONDS."""
    if not is_running(pid, name):
        return
    # The process can end between the check and the signal, such as when the watcher and scripts/status end the
    # same agent at once.
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + STOP_SECONDS
        while time.time() < deadline:
            if not is_running(pid, name):
                return
            time.sleep(0.2)
        os.kill(pid, signal.SIGKILL)


def pid_in(path: Path) -> int | None:
    """The process ID that a script saved in the file, if the file is there. The script deletes it when it ends,
    which can happen while another script reads it."""
    try:
        return int(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def is_running(pid: int, name: str) -> bool:
    """Whether the process's command line has `name` as a word, such as the program "watch" whatever path started
    it, or an agent's ID. Once a process has ended, its ID can go to another program, such as after a restart."""
    words = " ".join(command_line(pid)).split()
    return name in (Path(word).name for word in words)


def save_agent(agent: dict) -> None:
    AGENTS.mkdir(parents=True, exist_ok=True)
    write_atomically(AGENTS / f"{agent['id']}.json", json.dumps(agent))


def notify(agent_id: str, kind: str, message: str) -> None:
    """Leaves a message that the notice hook shows the agent once, after its next batch of tool calls. Each kind
    of notice, such as "wind-down", is shown at most once to an agent."""
    folder = NOTICES / agent_id
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / f"{kind}.told").exists():
        write_atomically(folder / kind, message)


def forget_notices(agent_id: str) -> None:
    """Drops the agent's notices, and which ones it was told, so that a new session gets each kind again."""
    shutil.rmtree(NOTICES / agent_id, ignore_errors=True)


def untold_notices(agent_id: str) -> list[str]:
    """The notices left for the agent that it hasn't seen, which this marks as seen."""
    folder = NOTICES / agent_id
    if not folder.is_dir():
        return []
    messages = []
    # A name that starts with a dot is a notice still being written.
    for notice in sorted(path for path in folder.iterdir() if path.suffix != ".told" and not path.name.startswith(".")):
        if not notice.with_suffix(".told").exists():
            messages.append(notice.read_text())
            notice.with_suffix(".told").touch()
    return messages


def transcript(account_name: str, session_id: str) -> Path | None:
    """Where the account's Claude Code keeps the session's transcript, if it has one yet. Claude Code names the
    folder after the session's working directory, replacing some characters and shortening long names, so the
    folder is found rather than built."""
    account = next((a for a in accounts() if a.name == account_name), None)
    if account is None:
        return None
    home = Path(os.path.expanduser(account.home)) if account.home else Path.home() / ".claude"
    return next(home.glob(f"projects/*/{session_id}.jsonl"), None)


def orchestrator() -> dict:
    """The orchestrator's account and model, which its session records."""
    return json.loads(ORCHESTRATOR.read_text()) if ORCHESTRATOR.exists() else {}


def this_session() -> int | None:
    """The process ID of the Claude Code session that runs this script, which Claude Code passes to its hooks."""
    value = os.environ.get("CLAUDE_PID")
    return int(value) if value else None


def orchestrator_elsewhere() -> int | None:
    """The process ID of an orchestrator that runs in another session of this checkout, if one does. A second
    session started in the main checkout would otherwise take over its records and its notices."""
    record = orchestrator()
    return record["pid"] if runs_elsewhere(record.get("pid"), record.get("started"), this_session()) else None


def runs_elsewhere(recorded: int | None, started: str | None, mine: int | None) -> bool:
    """Whether the recorded orchestrator still runs and isn't this session. Its start time tells it apart from a
    later process that got the same ID, such as an agent's session after a restart."""
    return bool(recorded) and recorded != mine and started_at(recorded) == started


def started_at(pid: int) -> str:
    """The process's start time, only to compare with another value from this function, or an empty string if no
    process has the ID."""
    if sys.platform == "linux":
        try:
            # The command's name, in parentheses, can hold spaces. The start time is the 20th field after it.
            after_name = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2]
            return after_name.split()[19]
        except (FileNotFoundError, ProcessLookupError):
            return ""
    record = sysctl(KERN_PROC, KERN_PROC_PID, pid)
    if not record:
        return ""
    # The process record starts with the start time, in seconds and microseconds.
    seconds, microseconds = struct.unpack_from("qi", record)
    return f"{seconds}.{microseconds:06d}"


def command_line(pid: int) -> list[str]:
    """The program and arguments that started the process, or an empty list if it has ended or, on macOS, belongs to
    another user."""
    if sys.platform == "linux":
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]
        except (FileNotFoundError, ProcessLookupError):
            return []
    else:
        # sysctl returns how many items the command line has, the program's full path, padding, then the command line,
        # each item ended by a zero byte.
        reply = sysctl(KERN_PROCARGS2, pid)
        count = int.from_bytes(reply[:4], sys.byteorder)
        after_path = reply[4:].partition(b"\0")[2]
        raw = after_path.lstrip(b"\0").split(b"\0")[:count]
    return [argument.decode(errors="replace") for argument in raw]


# The macOS sysctl names that ps reads. The agents' sandbox lets agents read them but refuses to start programs that run
# as root, such as ps.
CTL_KERN, KERN_PROC, KERN_PROC_PID, KERN_PROCARGS2 = 1, 14, 1, 49


def sysctl(*name: int) -> bytes:
    libc = ctypes.CDLL(None)
    full_name = (ctypes.c_int * (len(name) + 1))(CTL_KERN, *name)
    size = ctypes.c_size_t()
    # KERN_PROCARGS2 gives the same error, EINVAL, for an ended process and another user's, and neither is the loop's.
    if libc.sysctl(full_name, len(full_name), None, ctypes.byref(size), None, 0) != 0:
        return b""
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctl(full_name, len(full_name), buffer, ctypes.byref(size), None, 0) != 0:
        return b""
    return buffer.raw[: size.value]


def minutes_since_maintainer_wrote() -> float:
    if not MAINTAINER.exists():
        return float("inf")
    return (time.time() - json.loads(MAINTAINER.read_text())["last_message"]) / 60


def orchestrator_account() -> str | None:
    return orchestrator().get("account")


def agent_fields(agent_type: str) -> dict[str, str | list[str]]:
    """The fields at the top of an agent type's definition in .claude/agents/, such as its model. A list written as
    lines that start with "-" comes as a list, and one written on one line, such as "[building, code]", as text."""
    path = ROOT / ".claude" / "agents" / f"{agent_type}.md"
    fields: dict[str, str | list[str]] = {}
    key = ""
    if path.exists():
        for line in path.read_text().split("---")[1].splitlines():
            if line.strip().startswith("-") and key:
                fields[key] = [*(fields[key] or []), line.strip().removeprefix("-").strip()]
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    return fields


def agent_config(agent_type: str) -> str:
    """The model and effort that an agent type runs with, such as "opus/medium", from its definition."""
    fields = agent_fields(agent_type)
    return f"{fields.get('model', 'default')}/{fields.get('effort', 'default')}"


def agent_skills(agent_type: str) -> list[str]:
    """The skills that an agent type preloads, from its definition."""
    skills = agent_fields(agent_type).get("skills", "")
    if isinstance(skills, str):
        skills = skills.strip("[]").split(",")
    return [skill.strip() for skill in skills if skill.strip()]
