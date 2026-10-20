"""Runs an agent as a headless Claude Code session in a worktree of its own, for scripts/launch."""

import json
import os
import select
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import measurements
import usage

ROOT = usage.ROOT
WORKTREES = usage.LOOP / "worktrees"
# The agent counts as done once nothing has come from it for this long after its report.
QUIET_SECONDS = 60


@dataclass
class Session:
    agent_id: str
    account: str
    worktree: Path
    process: subprocess.Popen
    input_path: Path
    transcript: Path
    report: str = ""
    reported: bool = False
    last_output: float = field(default_factory=time.time)


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_worktree(agent_id: str, branch: str | None) -> Path:
    """A worktree at the latest main, or on an existing branch for a successor."""
    path = WORKTREES / agent_id
    git("fetch", "--quiet", "origin")
    if branch is None:
        git("worktree", "add", "--quiet", "--detach", str(path), "origin/main")
        return path
    for line in git("worktree", "list", "--porcelain").split("\n\n"):
        if f"branch refs/heads/{branch}" in line:
            git("worktree", "remove", "--force", line.splitlines()[0].removeprefix("worktree "))
    git("worktree", "add", "--quiet", "-B", branch, str(path), f"origin/{branch}")
    return path


def settings() -> str:
    """The project's permissions, given again on the command line. A headless session loads the project's own
    settings from its worktree, but ignores their allow rules in a folder its account hasn't trusted."""
    project = json.loads((ROOT / ".claude" / "settings.json").read_text())
    return json.dumps({"permissions": project["permissions"]})


def start(account: usage.Account, agent_type: str, agent_id: str, worktree: Path, prompt: str) -> Session:
    session_id = str(uuid.uuid4())
    input_path = usage.AGENTS / f"{agent_id}.input"
    usage.AGENTS.mkdir(parents=True, exist_ok=True)
    os.mkfifo(input_path)
    # Opened for reading and writing, so that the agent's input stays open between messages.
    input_fd = os.open(input_path, os.O_RDWR)
    environment = usage.environment_for(account) | {"LOOP_AGENT_ID": agent_id, "DISABLE_AUTO_COMPACT": "1"}
    command = [
        "claude", "-p", "--agent", agent_type, "--input-format", "stream-json", "--output-format", "stream-json",
        "--verbose", "--settings", settings(), "--session-id", session_id,
        "--name", agent_id,
    ]  # fmt: skip
    process = subprocess.Popen(
        command, cwd=worktree, env=environment, stdin=input_fd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    home = Path(environment.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    transcript = home / "projects" / str(worktree).replace("/", "-").replace(".", "-") / f"{session_id}.jsonl"
    session = Session(agent_id, account.name, worktree, process, input_path, transcript)
    send(input_path, prompt)
    return session


def send(input_path: Path, text: str) -> None:
    """Gives the agent a message, which it reads after its current tool call."""
    line = json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"
    descriptor = os.open(input_path, os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(descriptor, line.encode())
    finally:
        os.close(descriptor)


def handle(session: Session, line: bytes) -> None:
    event = json.loads(line) if line.startswith(b"{") else {}
    session.last_output = time.time()
    if event.get("type") == "rate_limit_event":
        usage.record_claude_event(session.account, event)
    elif event.get("type") == "result":
        # A session can give several results, such as after a background command finishes. The last one counts.
        session.report = event.get("result", "")
        session.reported = True


def runs_background_commands(process: subprocess.Popen) -> bool:
    children = subprocess.run(["pgrep", "-P", str(process.pid)], capture_output=True, text=True, check=False)
    return bool(children.stdout.strip())


def is_done(session: Session) -> bool:
    quiet = time.time() - session.last_output > QUIET_SECONDS
    return session.reported and quiet and not runs_background_commands(session.process)


def wait(session: Session, record: dict) -> str:
    while session.process.poll() is None and not is_done(session):
        if select.select([session.process.stdout], [], [], 5)[0]:
            line = session.process.stdout.readline()
            if line:
                handle(session, line)
    return finish(session, "done")


def finish(session: Session, why: str) -> str:
    """Stops the agent, saves the work left in its worktree to its branch, and returns its report."""
    if session.process.poll() is None:
        session.process.terminate()
        session.process.wait(timeout=30)
    saved = save_work(session.worktree, session.agent_id)
    session.input_path.unlink(missing_ok=True)
    record = json.loads((usage.AGENTS / f"{session.agent_id}.json").read_text())
    record["stopped"] = time.time()
    usage.save_agent(record)
    if "told_to_wind_down" in record:
        minutes = (record["stopped"] - record["told_to_wind_down"]) / 60
        measurements.record(f"reaction/{record['config']}", minutes)
    report = session.report or f"{session.agent_id} ended without a report."
    text = f"{session.agent_id} {why}.\n\n{report}" + (f"\n\n{saved}" if saved else "")
    if why == "stopped":
        print(text)
        sys.exit(0)
    return text


def save_work(worktree: Path, agent_id: str) -> str:
    """Commits and pushes what the agent left uncommitted, so that a successor or a person can carry on."""
    if not git("status", "--porcelain", cwd=worktree):
        return ""
    branch = git("branch", "--show-current", cwd=worktree) or f"unfinished-{agent_id}"
    if not git("branch", "--show-current", cwd=worktree):
        git("switch", "--quiet", "-c", branch, cwd=worktree)
    git("add", "--all", cwd=worktree)
    git("commit", "--quiet", "--no-verify", "-m", "chore: save unfinished work", cwd=worktree)
    git("push", "--quiet", "--set-upstream", "origin", branch, cwd=worktree)
    return f"Its unfinished work was saved and pushed to {branch}."
