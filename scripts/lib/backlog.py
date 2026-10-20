"""Which issues the loop builds next, and with which agent type."""

from dataclasses import dataclass

AGENT_FOR_LABEL = {"small": "builder-small", "deep": "builder-deep", "research": "researcher"}
WAITING_LABELS = {"needs-decision", "needs-triage"}


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    labels: frozenset[str]
    author_is_maintainer: bool
    open_blockers: tuple[int, ...]


def agent_type(issue: Issue) -> str:
    for label, agent in AGENT_FOR_LABEL.items():
        if label in issue.labels:
            return agent
    return "builder"


def ready(issues: list[Issue], in_progress: set[int]) -> list[Issue]:
    """The issues to build, oldest first: written by a maintainer, waiting for nothing and not started yet.

    Only one `loop` issue is in progress at a time, so that each change to the loop is tested on top of the last.
    """
    loop_busy = any("loop" in issue.labels and issue.number in in_progress for issue in issues)
    result = []
    for issue in sorted(issues, key=lambda issue: issue.number):
        is_loop = "loop" in issue.labels
        if (
            issue.number in in_progress
            or not issue.author_is_maintainer
            or issue.labels & WAITING_LABELS
            or issue.open_blockers
            or (is_loop and loop_busy)
        ):
            continue
        result.append(issue)
        loop_busy = loop_busy or is_loop
    return result
