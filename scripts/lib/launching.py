"""Runs an agent as a headless Claude Code session in a worktree of its own, for scripts/launch."""

import contextlib
import fcntl
import json
import os
import select
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import github
import measurements
import meter
import metering
import usage

ROOT = usage.ROOT
WORKTREES = usage.LOOP / "worktrees"
# Agents' own uv cache and tools. The scripts and hooks that run outside the sandbox use the default cache and Python, which
# agents may only read, so that nothing an agent writes runs outside the sandbox.
UV_CACHE = usage.LOOP / "agent-uv-cache"
# The agent counts as done once nothing has come from it for this long after its report.
QUIET_SECONDS = 60


@dataclass
class Session:
    agent_id: str
    session_id: str
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


def choose_account(
    decisions: dict[str, meter.Decision], accounts: list[usage.Account], requested: str | None, force: bool
) -> tuple[usage.Account | None, str]:
    """The account to start an agent on: the one requested if the meter gives it room, or else the one the meter
    names. Without one, the reason why the agent can't start."""
    name = requested or metering.next_account(decisions, accounts)
    if name is None:
        return None, "No account has room for an agent now.\n\n" + metering.report(decisions, accounts)
    account = next((a for a in accounts if a.name == name), None)
    if account is None:
        return None, f"No account is called {name}. .loop/accounts.json lists the accounts."
    if account.provider not in metering.LAUNCHABLE_PROVIDERS:
        return None, f"{name} is a {account.provider} account, and scripts/launch can't start agents there yet."
    if not decisions[name].room and not force:
        return None, (
            f"The meter gives {name} no room for an agent now. Run this again without --account to start on the "
            f"account it names.\n\n{metering.report(decisions, accounts)}"
        )
    return account, ""


def builds(agent_type: str) -> bool:
    """Whether the agent type builds an issue on a branch of its own, which the launcher makes and saves."""
    return "building" in usage.agent_skills(agent_type)


def built(record: dict) -> bool:
    """Whether the agent of a launch record builds. A record without the answer counts as a builder's, so that its
    work is kept."""
    return record.get("builds", True)


def busy_worktree(path: Path, branch: str, running: list[dict]) -> str:
    """Why a worktree that holds a branch can't be given to a new agent, or nothing if it can."""
    if path.parent != WORKTREES:
        return (
            f"{branch} is checked out in {path}, which isn't one of the loop's worktrees. Ask the maintainer to "
            "switch it to another branch, then launch again."
        )
    if any(Path(agent["worktree"]) == path for agent in running):
        return f"An agent still works in {path}. Wait for it to stop, or stop it, before you start another one."
    return ""


def make_worktree(agent_id: str, branch: str | None = None, continues: bool = False) -> Path:
    """A worktree at the latest main, detached or on a new branch, or with `continues`, on the branch as it was
    pushed. A stopped agent's worktree that holds the branch is saved and removed first. The launcher records the
    branch's upstream, because the sandbox doesn't let the agent write .git/config."""
    path = WORKTREES / agent_id
    with git_lock():
        if branch is None:
            git("fetch", "--quiet", "origin")
            git("worktree", "add", "--quiet", "--detach", str(path), "origin/main")
            return path
        for old in worktrees_on(branch):
            if reason := busy_worktree(old, branch, usage.running_agents()):
                raise SystemExit(reason)
            try:
                merged = is_merged(old, branch)
            except SystemExit as failure:
                raise SystemExit(
                    f"GitHub couldn't say whether {branch} merged, so the agent didn't start: {failure}. Launch it "
                    "again later."
                ) from None
            if merged and git("status", "--porcelain", cwd=old):
                raise SystemExit(f"The agent didn't start. {merged_leftovers(branch, old)}")
            if merged:
                git("worktree", "remove", "--force", str(old))
                continue
            if error := save_work(old):
                raise SystemExit(
                    f"The agent didn't start, because the last builder's work on {branch} isn't on GitHub yet. "
                    + push_advice(error, old)
                )
            git("worktree", "remove", "--force", str(old))
        # Fetched after saving, so that a successor starts from the work just pushed.
        git("fetch", "--quiet", "--prune", "origin")
        pushed = git("branch", "--remotes", "--list", f"origin/{branch}")
        if pushed and not continues:
            # A builder that starts from main couldn't push over the branch, because the ruleset refuses force pushes.
            raise SystemExit(f"{branch} exists on GitHub. Launch with --successor to continue it.")
        start = f"origin/{branch}" if pushed else "origin/main"
        git("worktree", "add", "--quiet", "--no-track", "-B", branch, str(path), start)
        git("config", f"branch.{branch}.remote", "origin")
        git("config", f"branch.{branch}.merge", f"refs/heads/{branch}")
        return path


@contextlib.contextmanager
def git_lock():
    """Lets one launcher at a time change the worktrees, because git refuses to change its config or refs while
    another git process holds their lock."""
    usage.LOOP.mkdir(exist_ok=True)
    with open(usage.LOOP / "git.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def worktrees_on(branch: str) -> list[Path]:
    return holding(git("worktree", "list", "--porcelain"), branch)


def holding(porcelain: str, branch: str) -> list[Path]:
    """The worktrees in `git worktree list --porcelain` output that have the branch checked out."""
    blocks = [block.splitlines() for block in porcelain.split("\n\n")]
    return [Path(lines[0].removeprefix("worktree ")) for lines in blocks if f"branch refs/heads/{branch}" in lines]


def settings() -> str:
    """The project's permissions and sandbox, given again on the command line. A headless session loads the
    project's own settings from its worktree, but ignores their allow rules in a folder its account hasn't
    trusted. The sandbox also lets the agent write its own uv cache, and the agent can't ask to run a command
    outside it, because the allow rules would approve that without asking."""
    project = json.loads((ROOT / ".claude" / "settings.json").read_text())
    sandbox = project["sandbox"] | {"allowUnsandboxedCommands": False}
    filesystem = sandbox.setdefault("filesystem", {})
    filesystem["allowWrite"] = [*filesystem.get("allowWrite", []), str(UV_CACHE)]
    return json.dumps({"permissions": project["permissions"], "sandbox": sandbox})


def transcript_path(account: usage.Account, worktree: Path, session_id: str) -> Path:
    home = Path(usage.environment_for(account).get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    return home / "projects" / str(worktree).replace("/", "-").replace(".", "-") / f"{session_id}.jsonl"


def start(
    account: usage.Account, agent_type: str, agent_id: str, worktree: Path, prompt: str, session_id: str
) -> Session:
    """Starts the agent's session, or continues it with its whole context if it has run before."""
    transcript = transcript_path(account, worktree, session_id)
    input_path = usage.AGENTS / f"{agent_id}.input"
    usage.AGENTS.mkdir(parents=True, exist_ok=True)
    input_path.unlink(missing_ok=True)
    os.mkfifo(input_path)
    # Opened for reading and writing, so that the agent's input stays open between messages.
    input_fd = os.open(input_path, os.O_RDWR)
    environment = usage.environment_for(account) | {
        "LOOP_AGENT_ID": agent_id,
        "DISABLE_AUTO_COMPACT": "1",
        # The hooks run outside the sandbox, so they run from the main checkout, never from the agent's own copy.
        "LOOP_ROOT": str(ROOT),
        "UV_CACHE_DIR": str(UV_CACHE),
        "UV_TOOL_DIR": str(UV_CACHE / "tools"),
    }
    command = [
        "claude", "-p", "--agent", agent_type, "--input-format", "stream-json", "--output-format", "stream-json",
        "--verbose", "--settings", settings(), "--name", agent_id,
        *(["--resume", session_id] if transcript.exists() else ["--session-id", session_id]),
    ]  # fmt: skip
    process = subprocess.Popen(
        command, cwd=worktree, env=environment, stdin=input_fd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    session = Session(agent_id, session_id, account.name, worktree, process, input_path, transcript)
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
    # A second finish, started by a SIGTERM while this one holds the git lock, would wait for the lock forever.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if session.process.poll() is None:
        session.process.terminate()
        session.process.wait(timeout=30)
    session.input_path.unlink(missing_ok=True)
    record = json.loads((usage.AGENTS / f"{session.agent_id}.json").read_text())
    with git_lock():
        saved = saved_work(session.worktree) if built(record) else remove_worktree(session.worktree)
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


def saved_work(worktree: Path) -> str:
    """What the orchestrator needs to know about a stopped builder's work, once it's saved."""
    branch = git("branch", "--show-current", cwd=worktree)
    changed = git("status", "--porcelain", cwd=worktree)
    if not branch or not (changed or unpushed(worktree)):
        return ""
    try:
        merged = is_merged(worktree, branch)
    except SystemExit as failure:
        return (
            f"Its work isn't on GitHub yet, because GitHub couldn't say whether {branch} merged: {failure}. Your next "
            "session start tries again."
        )
    if merged and changed:
        return merged_leftovers(branch, worktree)
    if merged:
        return ""
    if error := save_work(worktree):
        return f"Its work isn't on GitHub yet. {push_advice(error, worktree)}"
    return f"Its unfinished work was saved and pushed to {branch}."


def is_merged(worktree: Path, branch: str) -> bool:
    """Whether the worktree's commit is one of a merged pull request's commits, so that pushing it would bring back
    the branch that GitHub deleted at the merge. It may be an earlier one, such as when someone added a commit to the
    pull request on GitHub. The branch name alone can't tell, because a reopened issue's builder works on a new branch
    of the same name, which starts from main. Exits with gh's error when GitHub can't be asked."""
    merged = github.gh_json("pr", "list", "--head", branch, "--state", "merged", "--json", "commits,headRefOid") or []
    # gh lists only a pull request's first 100 commits, so the head commit is added too.
    commits = {commit["oid"] for pr in merged for commit in pr["commits"]} | {pr["headRefOid"] for pr in merged}
    return git("rev-parse", "HEAD", cwd=worktree) in commits


def merged_leftovers(branch: str, worktree: Path) -> str:
    return (
        f"{branch} merged, and its worktree {worktree} still has changed files that nothing will push. Builders on the "
        "issue are refused until the maintainer copies out what they need and removes the worktree. Tell the "
        "maintainer."
    )


def push_error(stderr: str) -> str:
    """A failed push's output on one line, without git's advice, which suggests pulling and pushing by hand."""
    advice = ("hint:", "To ", "Please make sure", "and the repository exists")
    kept = [line.strip() for line in stderr.splitlines() if line.strip() and not line.strip().startswith(advice)]
    return " ".join(" ".join(kept or [stderr]).split()).rstrip(".")


def push_advice(error: str, worktree: Path) -> str:
    """What to do about work that a failed push left only on this machine. A refused push, such as after a rebase or
    under a GitHub ruleset, is refused every time, so only a person can settle it. Other failures, such as a
    network error or an expired login, hit the whole loop rather than one issue, so they're only retried here."""
    if "[rejected]" in error or "[remote rejected]" in error:
        return f"The push was refused: {error}. Tell the maintainer. The work stays in {worktree}."
    return (
        f"The push failed: {error}. The next launch on the issue, or your next session start that reaches GitHub, "
        "pushes it again."
    )


def save_work(worktree: Path) -> str:
    """Commits what a builder left uncommitted on its branch, and pushes every commit GitHub doesn't have yet, so
    that a successor or a person can carry on. Returns git's error if the push fails, otherwise nothing."""
    branch = git("branch", "--show-current", cwd=worktree)
    if not branch:
        return ""
    if git("status", "--porcelain", cwd=worktree):
        git("add", "--all", cwd=worktree)
        git("commit", "--quiet", "--no-verify", "-m", "chore: save unfinished work", cwd=worktree)
    if not unpushed(worktree):
        return ""
    try:
        git("push", "--quiet", "--set-upstream", "origin", branch, cwd=worktree)
    except subprocess.CalledProcessError as error:
        return push_error(error.stderr)
    return ""


def unpushed(worktree: Path) -> int:
    """How many of the worktree's commits are on no branch on GitHub."""
    return int(git("rev-list", "--count", "HEAD", "--not", "--remotes=origin", cwd=worktree))


def remove_worktree(worktree: Path) -> str:
    """Removes the worktree of an agent that doesn't build, such as a reviewer. What it leaves there is scratch
    work, because its result is a review or a comment."""
    git("worktree", "remove", "--force", str(worktree))
    return ""
