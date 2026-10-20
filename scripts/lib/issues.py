"""Checks an issue's body against the `writing` and `planning` skills."""

from prose import lines
from pull_request import NARRATION

HEADINGS = ("## What should change", "## Why", "## Done when")


def check_body(body: str) -> list[str]:
    present = [line.strip() for line in body.splitlines()]
    problems = [f'Add the section "{heading}".' for heading in HEADINGS if heading not in present]
    for line in lines(body):
        if match := NARRATION.search(line.text):
            problems.append(f'"{match.group()}" tells how the work went. Say what should change and why instead.')
    return problems
