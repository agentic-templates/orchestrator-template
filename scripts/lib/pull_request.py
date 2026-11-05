"""Checks a pull request's title and description against AGENTS.md and the `writing` skill."""

import re

from prose import lines, prose_words

TYPES = ("feat", "fix", "docs", "test", "refactor", "perf", "build", "ci", "revert", "chore")
TITLE = re.compile(r"^(" + "|".join(TYPES) + r")(\([^)]+\))?!?: \S")
CLOSES = re.compile(r"^\s*(closes|fixes|resolves) #(\d+)\s*$", re.IGNORECASE | re.MULTILINE)
# Past this, a description describes the code or the history of the branch. Research results are exempt.
# Measured on merged pull requests of the templates this one came from: it blocks almost only those.
MAX_PROSE_WORDS = 500
MAX_CELL_WORDS = 40
MAX_PATH_LINES = 2
PATH_ITEM = re.compile(r"^\s*[-*+]\s+`?[\w./-]+/[\w./-]+`?[:,]?\s*$|^\s*[-*+]\s+`?[\w-]+\.\w{1,5}`?[:,]?\s*$")
NARRATION = re.compile(
    r"\b\d{1,2}:\d{2}\b|\b(this|last|earlier|previous|the first|the second) session\b"
    r"|\b(fix|review) rounds?\b|\bround \d\b",
    re.IGNORECASE,
)


def check_title(title: str) -> list[str]:
    if TITLE.match(title):
        return []
    return [f'The title "{title}" doesn\'t follow "<type>: <what changed>". The types are {", ".join(TYPES)}.']


def closed_issues(body: str) -> list[int]:
    return [int(match.group(2)) for match in CLOSES.finditer(body)]


def check_body(body: str, is_research: bool) -> list[str]:
    """The problems in a description. `is_research` says whether the issue it closes is a research issue."""
    problems = []
    if len(closed_issues(body)) != 1:
        problems.append('Start the description with one line "Closes #<issue>", for the one issue it builds.')
    if not is_research and prose_words(body) > MAX_PROSE_WORDS:
        problems.append(
            "The description is longer than a later reader will take in. Cut the sentences that describe "
            "how the code works, repeat the issue or tell the history of the branch."
        )
    path_lines = [line for line in body.splitlines() if PATH_ITEM.match(line)]
    if len(path_lines) > MAX_PATH_LINES:
        problems.append("Remove the list of changed files. The diff shows them.")
    for line in lines(body):
        if match := NARRATION.search(line.text):
            problems.append(
                f'"{match.group()}" tells how the work went. Describe the final change instead, as if it had '
                "been made in one go."
            )
        if line.kind == "table" and any(len(cell.split()) > MAX_CELL_WORDS for cell in line.text.split("|")):
            problems.append(f"Line {line.number} holds a paragraph in a table cell. Put sentences outside tables.")
    return problems
