"""Reads Claude Code's screen, as `tmux capture-pane -p -e` gives it, for scripts/send-clear.

The input box is the text between the last two rule lines, and starts with "❯" and a no-break space. Its
placeholder is drawn dim. A running turn shows a line above the box such as "✶ Thinking… (17s · ↓ 88 tokens)",
and a finished one shows "✻ Worked for 23s". A dialog, such as a permission prompt, replaces the box.
"""

import re
from dataclasses import dataclass

ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RULE = re.compile(r"^─{10,}")
PROMPT = "❯\xa0"
DIM = "\x1b[2m"
SPINNER = re.compile(r"^\S\s\S.*…\s\(\d")
# The spinner sits just above the box, below any messages queued during the turn.
SPINNER_LINES = 4


@dataclass(frozen=True)
class Screen:
    box: str | None  # the text typed in the input box, or None when no box shows
    is_busy: bool  # whether a turn is running


def plain(line: str) -> str:
    return ESCAPE.sub("", line)


def read(capture: str) -> Screen:
    lines = capture.rstrip("\n").split("\n")
    rules = [index for index, line in enumerate(lines) if RULE.match(plain(line))]
    if len(rules) < 2 or not plain(lines[rules[-2] + 1]).startswith(PROMPT):
        return Screen(None, is_busy(lines))
    top, bottom = rules[-2], rules[-1]
    return Screen(box_text(lines[top + 1 : bottom]), is_busy(lines[:top]))


def box_text(lines: list[str]) -> str:
    first = lines[0]
    after_prompt = first[first.index(PROMPT) + len(PROMPT) :]
    if after_prompt.startswith(DIM) and len(lines) == 1:
        return ""
    text = [plain(after_prompt)] + [plain(line).removeprefix("  ") for line in lines[1:]]
    return "\n".join(text).strip()


def is_busy(lines: list[str]) -> bool:
    shown = [plain(line) for line in lines if plain(line).strip()]
    return any(SPINNER.match(line) for line in shown[-SPINNER_LINES:])
