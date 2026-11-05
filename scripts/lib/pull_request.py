"""Checks a pull request's title and description against AGENTS.md and the `writing` skill."""

import re

from prose import lines, prose_words

TYPES = ("feat", "fix", "docs", "test", "refactor", "perf", "build", "ci", "revert", "chore")
TITLE = re.compile(r"^(" + "|".join(TYPES) + r")(\([^)]+\))?!?: \S")
CLOSES = re.compile(r"^\s*(closes|fixes|resolves) #(\d+)\s*$", re.IGNORECASE | re.MULTILINE)
# Measured on 169 merged pull requests of agentic-templates/agent-template, the limit blocked 3. Two mostly described
# the code. The third was a research result, which is why research results are exempt.
MAX_PROSE_WORDS = 500
MAX_CELL_WORDS = 40
MAX_PATH_LINES = 2
PATH_ITEM = re.compile(r"^\s*[-*+]\s+`?[\w./-]+/[\w./-]+`?[:,]?\s*$|^\s*[-*+]\s+`?[\w-]+\.\w{1,5}`?[:,]?\s*$")
NARRATION = re.compile(
    r"\b\d{1,2}:\d{2}\b|\b(this|last|earlier|previous|the first|the second) session\b"
    r"|\b(fix|review) rounds?\b|\bround \d\b",
    re.IGNORECASE,
)
# Bold text at the start of a line that stands as a heading: alone on its line, or ending in a colon.
BOLD_HEADING = re.compile(r"^\s*(\*\*|__)[^*_]+(\1\s*$|:\1|\1:)")


def check_title(title: str) -> list[str]:
    if TITLE.match(title):
        return []
    return [f'The title "{title}" doesn\'t follow "<type>: <what changed>". The types are {", ".join(TYPES)}.']


def closed_issues(body: str) -> list[int]:
    return [int(match.group(2)) for match in CLOSES.finditer(body)]


def check_body(body: str, is_research: bool) -> list[str]:
    """The problems in a description. `is_research` says whether the issue it closes is a research issue."""
    problems = []
    issues = closed_issues(body)
    if not issues:
        problems.append('Start the description with a line "Closes #<issue>", for the issue the pull request builds.')
    elif len(issues) > 1:
        numbers = ", ".join(f"#{issue}" for issue in issues)
        problems.append(
            f"The description closes several issues, with one line for each: {numbers}. Keep only the line for the "
            "issue the pull request builds."
        )
    words = prose_words(body)
    if not is_research and words > MAX_PROSE_WORDS:
        problems.append(
            f"The description has {words} words outside tables and code, and the limit is {MAX_PROSE_WORDS}. Cut the "
            "sentences that describe how the code works, repeat the issue or tell the history of the branch."
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
        if line.kind == "heading" or (line.kind == "prose" and BOLD_HEADING.match(line.text)):
            problems.append(
                f"Line {line.number} is a heading, or starts with bold text that stands as one. Write the description "
                "without them, in sentences and lists."
            )
        if line.kind == "table" and any(len(cell.split()) > MAX_CELL_WORDS for cell in line.text.split("|")):
            problems.append(
                f"Line {line.number} has a table cell of more than {MAX_CELL_WORDS} words. Move the long text out of "
                "the table."
            )
    return problems
