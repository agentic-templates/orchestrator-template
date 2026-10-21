"""The orchestrator's context refresh: the checks on its saved state, its request to refresh, and whether the
request still holds."""

import json
import re
import time
from datetime import datetime
from pathlib import Path

import usage

STATE = usage.LOOP / "state.md"
REQUEST = usage.LOOP / "refresh-request"
CHECKED = usage.LOOP / "refresh-checked"
WAITING = usage.LOOP / "refresh-waiting"
# Set when the watcher reports a failed refresh. Another request would only fail again, so none is taken until a
# new session starts, which the maintainer's own /clear does.
FAILED = usage.LOOP / "refresh-failed"
# How many refresh requests in a row found the state long. Kept across sessions, and reported to the maintainer
# privately, because the state holds their plans and the repository's issues are public.
LONG_STATES = usage.LOOP / "long-states"
LOG = usage.LOOP / "metrics" / "refreshes.jsonl"
# Real working state has never needed more than about 2,500 characters. Past this, the state likely holds what
# scripts or issues hold, so the refresh shows it for a check, but still happens.
LONG_STATE = 6_000
# A line that names this many issues or pull requests is likely a list that a script can give.
SEVERAL_REFERENCES = 3
REFERENCE = re.compile(r"(?<![\w&])#(\d+)\b")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
# The end of a transcript that is read to find whether a turn is running, enough for several turns.
TAIL_BYTES = 512 * 1024
COMMAND_TAGS = ("<command-name>", "<local-command")

CHECKLIST = """\
Before the refresh, prune .loop/state.md. Delete each entry that:
- is done
- is no longer true
- an issue or a pull request now holds
- scripts/status lists, such as the running agents and the open pull requests

Keep the goal you're working on, the decisions that aren't in an issue yet, what you're waiting for, and each
temporary instruction from the maintainer that still holds, such as to start no agents until they say so."""


def references(text: str) -> set[int]:
    return {int(number) for number in REFERENCE.findall(text)}


def findings(text: str, closed: set[int]) -> list[str]:
    """What looks out of place in the saved state. Each has exceptions, so the orchestrator checks them."""
    found = []
    headings: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        named = references(line)
        for reference in sorted(named & closed):
            found.append(f"Line {number} names #{reference}, which is closed.")
        if len(named) >= SEVERAL_REFERENCES:
            found.append(
                f"Line {number} lists several issues or pull requests. Delete the ones that scripts/status lists."
            )
        if heading := HEADING.match(line):
            headings[heading.group(1)] = headings.get(heading.group(1), 0) + 1
    found += [
        f'The heading "{name}" appears {count} times. Merge them.' for name, count in headings.items() if count > 1
    ]
    if len(text) > LONG_STATE:
        found.append(
            "The state is longer than usual. Look for entries that an issue, a pull request or scripts/status holds."
        )
    return found


def request(transcript: str | None) -> None:
    REQUEST.write_text(json.dumps({"time": time.time(), "transcript": transcript}))
    characters = len(STATE.read_text()) if STATE.exists() else 0
    is_long = characters > LONG_STATE
    count = int(LONG_STATES.read_text()) + 1 if is_long and LONG_STATES.exists() else int(is_long)
    LONG_STATES.write_text(str(count))
    # Kept for the dashboard: the state's size at each refresh can't be found again later.
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as file:
        file.write(json.dumps({"time": time.time(), "state_characters": characters}) + "\n")


def load_request() -> dict | None:
    return json.loads(REQUEST.read_text()) if REQUEST.exists() else None


def clear() -> None:
    """Forgets the request and the checklist, as a new session starts."""
    for path in (REQUEST, CHECKED, WAITING, FAILED):
        path.unlink(missing_ok=True)


def tool_calls_since(transcript: Path, since: float) -> bool:
    """Whether the session called a tool after `since`, by the times its transcript gives each message."""
    if not transcript.exists():
        return False
    for line in transcript.read_bytes().splitlines():
        entry = json.loads(line) if line.startswith(b"{") else {}
        if entry.get("type") != "assistant" or not entry.get("timestamp"):
            continue
        content = (entry.get("message") or {}).get("content") or []
        calls_a_tool = any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content)
        if calls_a_tool and datetime.fromisoformat(entry["timestamp"]).timestamp() > since:
            return True
    return False


def turn_running(transcript: Path) -> bool:
    """Whether the session is in the middle of a turn: its last prompt, which includes a task notification, came
    after its last turn ended. Claude Code doesn't show its spinner while it writes a long answer, so the screen
    alone can't tell."""
    if not transcript.exists():
        return False
    with open(transcript, "rb") as file:
        file.seek(max(0, transcript.stat().st_size - TAIL_BYTES))
        lines = file.read().splitlines()
    last_prompt = last_end = ""
    for line in lines:
        entry = json.loads(line) if line.startswith(b"{") else {}
        content = (entry.get("message") or {}).get("content")
        # A slash command such as /clear is recorded like a prompt, but no turn follows it.
        if entry.get("type") == "user" and isinstance(content, str) and not content.startswith(COMMAND_TAGS):
            last_prompt = entry.get("timestamp", "")
        elif entry.get("type") == "system" and entry.get("subtype") == "turn_duration":
            last_end = entry.get("timestamp", "")
    return last_prompt > last_end


def is_cancelled(saved: dict) -> bool:
    """A tool call after the request means the session went on working, so its saved state may be out of date."""
    return bool(saved.get("transcript")) and tool_calls_since(Path(saved["transcript"]), saved["time"])
