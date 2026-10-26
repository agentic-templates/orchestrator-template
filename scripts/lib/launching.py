"""Runs an agent as a headless Claude Code session in a worktree of its own, for scripts/launch."""

import contextlib
import fcntl
import json
import os
import re
import select
import shlex
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import backlog
import contexts
import github
import measurements
import meter
import metering
import usage

ROOT = usage.ROOT
WORKTREES = usage.LOOP / "worktrees"
# Agents' own uv cache and tools. The scripts, hooks and MCP servers that run outside the sandbox use the default cache
# and Python, which agents may only read, so that nothing an agent writes runs outside the sandbox.
UV_CACHE = usage.LOOP / "agent-uv-cache"
# Places in the home folder that commonly hold secrets, which agents mustn't read. Claude Code's sandbox lets every
# command reach the keychain's services, so only denying Library/Keychains keeps commands from reading the logins in
# the login keychain, such as gh's and Claude Code's.
SECRETS = (
    ".netrc", ".npmrc", ".docker", ".kube", ".git-credentials", ".config/git/credentials", ".zsh_history",
    ".zsh_sessions", ".bash_history", ".local/share/fish", ".python_history", ".node_repl_history", "Library/Keychains",
)  # fmt: skip
# Characters that Claude Code's permission rules read as patterns or escapes, not as part of a name.
PATTERN_CHARACTERS = "[*?\\"
# The only folders in ~/.config that agents may read. Git exits with an error when it can't read its own.
CONFIG_NEEDED = ("gh", "git")
# A key's line names the host it's for, which agents then reach without the auto-mode classifier's review. The host
# stays on its key's line, so that removing the key also takes away the host.
AGENTS_ENV = usage.LOOP / "agents.env"
# A line as .env files write it: a value in quotes, or one that ends where a comment starts after a space. Other tools
# read a backslash in double quotes as an escape, so such a value isn't taken as written.
AGENT_VARIABLE = re.compile(
    r"(export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?P<value>'[^']*'|\"[^\"\\\\]*\"|[^'\"\s#]\S*(\s+[^\s#]\S*)*|)(\s+#\s*(?P<comment>.*))?"
)
# A host as the sandbox's allowedDomains takes it, which rejects a bare "*".
HOST = re.compile(r"(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:[0-9]+)?")
# Matched against the words between underscores in a name, so that KEYBOARD_LAYOUT isn't left out.
SECRET_WORDS = {"TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIALS"}
# The agent counts as done once nothing has come from it for this long after its report.
QUIET_SECONDS = 60
# Read from the main checkout, because a branch could add a server to its own copy.
MCP_JSON = ROOT / ".mcp.json"
# An MCP server's command runs outside the sandbox, so it starts as the hooks do: in the main checkout, without the
# agents' uv cache.
SERVER_START = 'cd "$1" && shift && exec env -u UV_CACHE_DIR -u UV_TOOL_DIR "$@"'
# Set high, so that only an issue that builders can't finish reaches it, such as one stuck on a question only the
# maintainer can settle. Each further session would reread the issue and the code for nothing.
BUILDER_SESSIONS_CAP = 10


@dataclass
class Session:
    agent_id: str
    session_id: str
    account: str
    worktree: Path
    process: subprocess.Popen
    input_path: Path
    report: str = ""
    reported: bool = False
    last_output: float = field(default_factory=time.time)
    # The issue's hand-off note, deleted once the agent gives a real answer.
    note: Path | None = None
    # The number of background commands and subagents that the session last reported as running.
    background_tasks: int = 0


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


# Every hook event in githooks(5). core.hooksPath stops only the hooks in a hooks folder. On git 2.56, turning off an
# event stops only the hooks set in git config (hook.<name>.command), though git-config(1) says it stops them all.
HOOK_EVENTS = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit", "prepare-commit-msg",
    "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge", "pre-push", "pre-receive", "update",
    "proc-receive", "post-receive", "post-update", "reference-transaction", "push-to-checkout", "pre-auto-gc",
    "post-rewrite", "sendemail-validate", "fsmonitor-watchman", "p4-changelist", "p4-prepare-changelist",
    "p4-post-changelist", "p4-pre-submit", "post-index-change",
)  # fmt: skip
# Git outside the sandbox would run code that an agent controls. A nested repository's config can name programs. And the
# maintainer's hooks run on the agent's work, such as a pre-push hook that runs the tests, or a post-checkout hook that
# installs the work's dependencies. These settings stop every hook and a nested repository's fsmonitor. They don't stop
# its filters, which git runs on a file's content as it reads the file. And `ignore = none` in .gitmodules makes
# `git status` read a nested repository, filters included, despite diff.ignoreSubmodules. So callers also leave what
# nested_repositories() finds out of `git status` and `git add`.
SAFE_GIT = (
    "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "diff.ignoreSubmodules=all",
    *(setting for event in HOOK_EVENTS for setting in ("-c", f"hook.{event}.enabled=false")),
)  # fmt: skip


def worktree_git(worktree: Path, *args: str) -> str:
    """Git in an agent's worktree, which the agent controls, from outside the sandbox."""
    return git(*SAFE_GIT, *args, cwd=worktree)


def nested_repositories(worktree: Path) -> list[str]:
    """The nested git repositories in the worktree: registered submodules, other repositories staged as gitlinks,
    and untracked ones. Neither `git ls-files` call reads a nested repository's config."""
    staged = worktree_git(worktree, "ls-files", "--stage", "-z").split("\0")
    gitlinks = [entry.split("\t", 1)[1] for entry in staged if entry.startswith("160000 ")]
    # git ls-files lists an untracked nested repository as its folder, ending in "/", and every other untracked file
    # on its own.
    untracked = worktree_git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return gitlinks + [path.removesuffix("/") for path in untracked if path.endswith("/")]


def changes(worktree: Path, nested: list[str]) -> str:
    """The worktree's changes, as `git status --porcelain` lists them, without its nested repositories."""
    return worktree_git(worktree, "status", "--porcelain", "--", *excluding(nested))


def excluding(nested: list[str]) -> list[str]:
    return [f":(exclude){path}" for path in nested]


def left_out(nested: list[str]) -> str:
    """What the orchestrator needs to know about the nested repositories a save leaves out."""
    note = "is a nested git repository, so the loop didn't save it. The loop deletes it when it removes the worktree."
    return " ".join(f"{path} {note}" for path in nested)


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


def refuse_builder(issue: int) -> str:
    """Returns why no builder may start on the issue, or nothing if one may. At the cap it first labels the issue
    needs-decision."""
    labels = github.gh_json("issue", "view", str(issue), "--json", "labels", "--jq", "[.labels[].name]")
    if backlog.NEEDS_DECISION in labels:
        return (
            f"Issue #{issue} waits for the maintainer's decision ({backlog.NEEDS_DECISION}), so the launcher started "
            "no builder. Once the maintainer has answered, rewrite the issue with the answer and remove the label. If "
            "the issue stays open, launch a builder on it again."
        )
    records = usage.agent_records()
    if builder_sessions(records, issue) < BUILDER_SESSIONS_CAP:
        return ""
    # Reading the issue's events costs another GitHub call, so it waits until all of the issue's records reach the cap.
    pages = github.gh_json("api", "--paginate", "--slurp", f"repos/{github.repository()}/issues/{issue}/events")
    events = [event for page in pages for event in page]
    if builder_sessions(records, issue, since=count_starts_at(events)) < BUILDER_SESSIONS_CAP:
        return ""
    github.gh("issue", "edit", str(issue), "--add-label", backlog.NEEDS_DECISION)
    note = note_path(issue)
    first_step = (
        f"Read its hand-off note in {note.relative_to(ROOT)}"
        if note.exists()
        else "Read the issue and its branch's commits"
    )
    return (
        f"Issue #{issue} has had many builder sessions, so the launcher labelled it {backlog.NEEDS_DECISION} and "
        f"started no builder. {first_step}, rewrite the issue to say what blocks it and the option you recommend, "
        "and notify the maintainer."
    )


def builder_sessions(records: list[dict], issue: int, since: float = 0.0) -> int:
    """How many builder sessions the records show for the issue that started after `since`, a time in seconds since
    the epoch."""
    return sum(1 for record in records if built(record) and record["number"] == issue and record["started"] > since)


def count_starts_at(events: list[dict]) -> float:
    """When the count of the issue's builder sessions starts: the latest time that needs-decision was removed or the
    issue was reopened, in seconds since the epoch, or 0 if neither happened."""
    times = [
        datetime.fromisoformat(event["created_at"]).timestamp()
        for event in events
        if event["event"] == "reopened"
        or (event["event"] == "unlabeled" and event.get("label", {}).get("name") == backlog.NEEDS_DECISION)
    ]
    return max(times, default=0.0)


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


def make_worktree(agent_id: str, issue: int | None = None, continues: bool = False) -> Path:
    """A worktree at the latest main, detached or on the issue's new branch, or with `continues`, on the issue's
    branch as it was pushed. It has an empty .loop/ for the agent's working files, which git ignores."""
    path = WORKTREES / agent_id
    with git_lock():
        if issue is None:
            git("fetch", "--quiet", "origin")
            git(*SAFE_GIT, "worktree", "add", "--quiet", "--detach", str(path), "origin/main")
        else:
            add_branch_worktree(path, issue, continues)
    (path / ".loop").mkdir()
    return path


def add_branch_worktree(path: Path, issue: int, continues: bool) -> None:
    """Adds a worktree on the issue's branch. A stopped agent's worktree that holds the branch is saved and removed
    first. The launcher records the branch's upstream, because the sandbox doesn't let the agent write .git/config."""
    branch = f"issue-{issue}"
    for old in worktrees_on(branch):
        if reason := busy_worktree(old, branch, usage.running_agents()):
            raise SystemExit(reason)
        try:
            merged = is_merged(old, branch)
        except SystemExit as failure:
            raise SystemExit(
                f"GitHub couldn't say whether {branch} merged, so the agent didn't start: {failure}. Launch it again "
                "later."
            ) from None
        if merged and changes(old, nested_repositories(old)):
            raise SystemExit(f"The agent didn't start. {merged_leftovers(branch, old)}")
        if merged:
            remove_worktree(old)
            continue
        collect_note(old, issue)
        if error := save_work(old):
            raise SystemExit(
                f"The agent didn't start, because the last builder's work on {branch} isn't on GitHub yet. "
                + push_advice(error, old)
            )
        remove_worktree(old)
    # Fetched after saving, so that a successor starts from the work just pushed.
    git("fetch", "--quiet", "--prune", "origin")
    pushed = git("branch", "--remotes", "--list", f"origin/{branch}")
    if pushed and not continues:
        # A builder that starts from main couldn't push over the branch, because the ruleset refuses force pushes.
        raise SystemExit(f"{branch} exists on GitHub. Launch with --successor to continue it.")
    start = f"origin/{branch}" if pushed else "origin/main"
    # A successor checks out the last builder's pushed work, so without SAFE_GIT a post-checkout hook would run on it.
    git(*SAFE_GIT, "worktree", "add", "--quiet", "--no-track", "-B", branch, str(path), start)
    git("config", f"branch.{branch}.remote", "origin")
    git("config", f"branch.{branch}.merge", f"refs/heads/{branch}")


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
    trusted. The sandbox also lets the agent write its own uv cache and reach the hosts in .loop/agents.env, and
    the agent can't ask to run a command outside it, because the allow rules would approve that without asking."""
    project = json.loads((ROOT / ".claude" / "settings.json").read_text())
    sandbox = project["sandbox"] | {"allowUnsandboxedCommands": False}
    filesystem = sandbox.setdefault("filesystem", {})
    filesystem["allowWrite"] = [*filesystem.get("allowWrite", []), str(UV_CACHE)]
    network = sandbox.setdefault("network", {})
    network["allowedDomains"] = [*network.get("allowedDomains", []), *agent_variables()[1]]
    secrets = [rule_path(path) for path in secret_paths()]
    # A path that may be a file or a folder is denied both ways.
    rules = [f"Read({path})" for path in secrets] + [f"Read({path}/**)" for path in secrets]
    deny = project["permissions"]["deny"]
    # The project's rules for its own files, such as .env files, match only in the agent's worktree, which has none of
    # those files. The maintainer's copies are in the main checkout.
    rules += [rule for rule in map(in_main_checkout, deny) if rule]
    permissions = project["permissions"] | {"deny": deny + rules}
    credentials = sandbox.setdefault("credentials", {})
    credentials["files"] = [*credentials.get("files", []), *({"path": path, "mode": "deny"} for path in secrets)]
    return json.dumps({"permissions": permissions, "sandbox": sandbox})


def in_main_checkout(rule: str) -> str | None:
    """The project's rule against reading or editing some of its own files, for the same files in the main
    checkout. Other rules, and rules for paths from the home folder or absolute ones, give None."""
    tool, _, pattern = rule.partition("(")
    if tool not in ("Read", "Edit") or pattern.startswith(("~/", "//")):
        return None
    pattern = pattern.removeprefix("./")
    # Claude Code matches a pattern without a leading slash from the working directory, and some, such as .env and
    # secrets/**, in every folder below it. Matching each pattern in every folder of the main checkout can only deny
    # more than the project's rule does.
    if not pattern.startswith(("/", "**/")):
        pattern = f"**/{pattern}"
    return f"{tool}({rule_path(ROOT)}/{pattern.removeprefix('/')}"


def secret_paths() -> list[Path]:
    """What agents mustn't read: the places that commonly hold secrets, everything in ~/.config but what their
    commands need, and every account's files. Claude Code reads its account's files outside the sandbox, so an
    agent's commands never need them."""
    home = Path.home()
    config = home / ".config"
    configs = [path for path in config.iterdir() if path.name not in CONFIG_NEEDED] if config.is_dir() else []
    accounts = [home / ".claude", home / ".claude.json", home / ".codex", *account_folders()]
    return sorted({*(home / path for path in SECRETS), *configs, *accounts})


def account_folders() -> list[Path]:
    """The folders of the accounts that .loop/accounts.json gives a folder of their own."""
    return [Path(os.path.expanduser(account.home)) for account in usage.accounts() if account.home]


def path_refusal() -> str:
    """Why no agent can start, if the path of the home folder, the main checkout or an account's folder contains a
    character that permission rules don't read as a plain character, because then the rules that deny agents secrets
    might not match. Otherwise an empty string."""
    for path in (Path.home(), ROOT, *account_folders()):
        if found := [character for character in PATTERN_CHARACTERS if character in str(path)]:
            return (
                f"No agent can start, because {path} contains {' and '.join(found)}, which permission rules don't read "
                "as plain characters, so the rules that keep agents from reading the secrets in it might not match "
                "those files. Ask the maintainer to rename or move it so that its path has none of them."
            )
    return ""


def agent_variables() -> tuple[dict[str, str], list[str]]:
    """The variables and hosts in .loop/agents.env. Raises ValueError if a line can't be read."""
    text = AGENTS_ENV.read_text() if AGENTS_ENV.exists() else ""
    return parse_agent_variables(text)


def parse_agent_variables(text: str) -> tuple[dict[str, str], list[str]]:
    """The variables that the lines of a .env file set, and the hosts that lines name in a comment that starts with
    "host:"."""
    variables, hosts = {}, []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = AGENT_VARIABLE.fullmatch(line)
        comment = (match and match["comment"]) or ""
        host = comment.removeprefix("host:").strip() if comment.startswith("host:") else None
        # The errors don't quote the line, because it may hold a key.
        if not match:
            raise ValueError(
                f"Line {number} of .loop/agents.env isn't NAME=value, as in \"DEEPSEEK_API_KEY=... # host: "
                'api.deepseek.com", or has a backslash inside double quotes. Ask the maintainer to fix the line.'
            )
        if host is not None and not HOST.fullmatch(host):
            raise ValueError(
                f'The host after "# host:" on line {number} of .loop/agents.env isn\'t a name such as *.example.com, '
                "without https://. Ask the maintainer to fix the line."
            )
        value = match["value"]
        variables[match["name"]] = value[1:-1] if value[:1] in ("'", '"') else value
        if host:
            hosts.append(host)
    return variables, hosts


def agent_variables_refusal() -> str:
    """Why no agent can start, if .loop/agents.env has a line it can't read. Otherwise an empty string."""
    try:
        agent_variables()
    except ValueError as error:
        return f"No agent can start. {error}"
    return ""


def without_secrets(environment: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The environment without the variables whose names may hold secrets, and those variables' names. Agents
    inherit the orchestrator's environment, where the maintainer may have exported a key."""

    def may_be_secret(name: str) -> bool:
        return not SECRET_WORDS.isdisjoint(name.upper().split("_"))

    kept = {name: value for name, value in environment.items() if not may_be_secret(name)}
    return kept, sorted(name for name in environment if may_be_secret(name))


def left_out_message(names: list[str]) -> str:
    return (
        f"Your environment leaves out {', '.join(names)}. If your work needs one of them, say so in your report, so "
        "that the maintainer can add it to .loop/agents.env."
    )


def github_login() -> dict[str, str]:
    """The environment that gives an agent's gh and git the maintainer's GitHub login, because the agent can't read
    the login keychain, where gh keeps it. The empty first entry clears the helpers that git's config files set, such
    as osxkeychain, so git asks only gh."""
    return {
        "GH_TOKEN": github.gh("auth", "token"),
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "!gh auth git-credential",
    }


def rule_path(path: Path) -> str:
    """The path as Claude Code's permission rules write it: from the home folder, or absolute with two slashes."""
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else f"/{path}"


def start(
    account: usage.Account, agent_type: str, agent_id: str, worktree: Path, prompt: str, session_id: str
) -> Session:
    """Starts the agent's session, or continues it with its whole context if it has run before."""
    input_path = usage.AGENTS / f"{agent_id}.input"
    usage.AGENTS.mkdir(parents=True, exist_ok=True)
    input_path.unlink(missing_ok=True)
    os.mkfifo(input_path)
    # Opened for reading and writing, so that the agent's input stays open between messages.
    input_fd = os.open(input_path, os.O_RDWR)
    inherited, secret_names = without_secrets(usage.environment_for(account))
    environment = {
        **inherited,
        **agent_variables()[0],
        **github_login(),
        "LOOP_AGENT_ID": agent_id,
        "DISABLE_AUTO_COMPACT": "1",
        # The hooks run outside the sandbox, so they run from the main checkout, never from the agent's own copy.
        "LOOP_ROOT": str(ROOT),
        "UV_CACHE_DIR": str(UV_CACHE),
        "UV_TOOL_DIR": str(UV_CACHE / "tools"),
    }
    resumes = bool(usage.transcript(account.name, session_id))
    # A headless session starts every MCP server it finds without asking. --strict-mcp-config leaves out the account's
    # own servers and claude.ai connectors, because through them a prompt injection could reach the services the
    # maintainer connected to Claude.
    command = [
        "claude", "-p", "--agent", agent_type, "--input-format", "stream-json", "--output-format", "stream-json",
        "--verbose", "--settings", settings(), "--strict-mcp-config", *mcp_arguments(), "--name", agent_id,
        *(["--resume", session_id] if resumes else ["--session-id", session_id]),
    ]  # fmt: skip
    dropped = [name for name in secret_names if name not in environment]
    if dropped and not resumes:
        prompt = f"{prompt}\n\n{left_out_message(dropped)}"
    process = subprocess.Popen(
        command, cwd=worktree, env=environment, stdin=input_fd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    session = Session(agent_id, session_id, account.name, worktree, process, input_path)
    send(input_path, prompt)
    return session


def mcp_arguments() -> list[str]:
    """The arguments that give an agent main's MCP servers. Raises ValueError if main's .mcp.json isn't valid JSON."""
    if not MCP_JSON.exists():
        return []
    servers = json.loads(MCP_JSON.read_text()).get("mcpServers", {})
    for server in servers.values():
        # Claude Code runs a local server's command and a remote server's headersHelper, which prints its request headers.
        if "command" in server:
            command = started_in_main([server["command"], *server.get("args", [])])
            server.update(command=command[0], args=command[1:])
        if "headersHelper" in server:
            server["headersHelper"] = shlex.join(started_in_main(["/bin/sh", "-c", server["headersHelper"]]))
    return ["--mcp-config", json.dumps({"mcpServers": servers})]


def started_in_main(command: list[str]) -> list[str]:
    return ["/bin/sh", "-c", SERVER_START, "sh", str(ROOT), *command]


def mcp_json_refusal() -> str:
    """Why no agent can start, if main's .mcp.json isn't valid JSON. Otherwise an empty string."""
    try:
        mcp_arguments()
    except ValueError as error:
        return f"No agent can start, because {MCP_JSON} isn't valid JSON: {error}. Ask the maintainer to fix that file."
    return ""


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
    # Deleted only on a real answer, so that a session that fails at once leaves the note for the next successor.
    is_answer = event.get("type") == "assistant" and not contexts.is_failed_request(event)
    if session.note and is_answer:
        session.note.unlink(missing_ok=True)
        session.note = None
    if event.get("type") == "rate_limit_event":
        usage.record_claude_event(session.account, event)
    elif event.get("subtype") == "background_tasks_changed":
        # Claude Code marks the tasks it starts itself as ambient, and some of them never end.
        session.background_tasks = len([task for task in event.get("tasks") or [] if not task.get("ambient")])
    elif event.get("type") == "result":
        # A session can give several results, such as after a background command finishes. The last one counts.
        session.report = event.get("result", "")
        session.reported = True
        usage.record_context_windows(session.account, event.get("modelUsage") or {})


def is_done(session: Session) -> bool:
    quiet = time.time() - session.last_output > QUIET_SECONDS
    # The agent's child processes don't count, because its MCP servers stay child processes for as long as the agent
    # runs.
    return session.reported and quiet and not session.background_tasks


def wait(session: Session, record: dict) -> str:
    stdout = session.process.stdout
    assert stdout is not None
    while session.process.poll() is None and not is_done(session):
        if select.select([stdout], [], [], 5)[0]:
            line = stdout.readline()
            if line:
                handle(session, line)
    return finish(session, "done")


def finish(session: Session, why: str) -> str:
    """Stops the agent, saves the work left in its worktree to its branch, and returns its report."""
    # A second finish, started by a SIGTERM while this one holds the git lock, would wait for the lock forever.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    usage.end(session.process.pid, session.agent_id)
    session.process.wait()
    session.input_path.unlink(missing_ok=True)
    record = json.loads((usage.AGENTS / f"{session.agent_id}.json").read_text())
    with git_lock():
        if built(record):
            collect_note(session.worktree, record["number"])
            saved = saved_work(session.worktree)
        else:
            saved = remove_worktree(session.worktree)
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


def note_path(number: int) -> Path:
    return usage.HANDOFF / f"issue-{number}.md"


def note_for(number: int, continues: bool) -> Path | None:
    """The issue's hand-off note for a builder that continues the issue, which the launcher deletes once the builder
    has answered. A builder that starts from main makes any note about earlier work on the issue wrong, so it's
    deleted."""
    note = note_path(number)
    if not continues:
        note.unlink(missing_ok=True)
    return note if continues and note.exists() else None


def successor_message(number: int, note: Path | None, commits: str) -> str:
    """The first message of a builder that continues the issue: the note of the builder before it, quoted, and the
    commits on its branch that main doesn't have, one line each."""
    if note:
        quoted = "\n".join(f"> {line}" for line in note.read_text().splitlines())
        text = f"Continue issue #{number}. The builder before you handed off with this note:\n\n{quoted}"
    else:
        text = f"Continue issue #{number}. The builder before you stopped without a hand-off note."
    if not commits:
        return f"{text}\n\nYour branch has no commits yet."
    return f"{text}\n\nThe commits on your branch so far:\n\n{commits}"


def branch_commits(worktree: Path) -> str:
    return worktree_git(worktree, "log", "--oneline", "origin/main..HEAD")


def collect_note(worktree: Path, number: int) -> None:
    """Moves the hand-off note a stopped builder left in its worktree to where the launcher gives it to the next
    builder on the issue. Only a plain file counts, because through a link the launcher, which runs outside the
    sandbox, would pass on a file that the builder can't read."""
    try:
        folder = os.open(worktree / usage.NOTE.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return
    try:
        text = plain_file_text(folder, usage.NOTE.name)
        if text is None:
            return
        usage.HANDOFF.mkdir(parents=True, exist_ok=True)
        note_path(number).write_text(text)
        os.unlink(usage.NOTE.name, dir_fd=folder)
    finally:
        os.close(folder)


def plain_file_text(folder: int, name: str) -> str | None:
    """The text of the file in the open folder, or None if it isn't a plain file with a single link."""
    try:
        # Without O_NONBLOCK, opening a named pipe would wait for a writer, holding the git lock.
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except OSError:
        return None
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(descriptor)
        return None
    with os.fdopen(descriptor, "rb") as file:
        return file.read().decode(errors="replace")


def saved_work(worktree: Path) -> str:
    """What the orchestrator needs to know about a stopped builder's work, once it's saved, and about what the save
    left out."""
    nested = nested_repositories(worktree)
    return " ".join(filter(None, [save_branch(worktree, nested), left_out(nested)]))


def save_branch(worktree: Path, nested: list[str]) -> str:
    """Saves a stopped builder's work on its branch, unless the branch merged, and says what happened."""
    branch = worktree_git(worktree, "branch", "--show-current")
    changed = changes(worktree, nested)
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
    return worktree_git(worktree, "rev-parse", "HEAD") in commits


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
    branch = worktree_git(worktree, "branch", "--show-current")
    if not branch:
        return ""
    nested = nested_repositories(worktree)
    if changes(worktree, nested):
        worktree_git(worktree, "add", "--all", "--", *excluding(nested))
        worktree_git(worktree, "commit", "--quiet", "--no-verify", "-m", "chore: save unfinished work")
    if not unpushed(worktree):
        return ""
    try:
        worktree_git(worktree, "push", "--quiet", "--set-upstream", "origin", branch)
    except subprocess.CalledProcessError as error:
        return push_error(error.stderr)
    return ""


def unpushed(worktree: Path) -> int:
    """How many of the worktree's commits are on no branch on GitHub."""
    return int(worktree_git(worktree, "rev-list", "--count", "HEAD", "--not", "--remotes=origin"))


def remove_worktree(worktree: Path) -> str:
    """Removes the worktree and everything left in it, such as a reviewer's scratch work or the nested repositories a
    save leaves out. A reviewer's scratch work can go, because its result is a review or a comment."""
    git(*SAFE_GIT, "worktree", "remove", "--force", str(worktree))
    return ""
