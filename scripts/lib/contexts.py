"""How big each session's context is, read from its transcript a little at a time.

Each check reads only what the transcript gained since the last one, so watching a long session costs no more
than watching a short one.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import usage

# Tokens a turn adds, until a session has had enough turns to show its own. An earlier project, zzz, measured it.
DEFAULT_GROWTH = 4_200
TURNS_BEFORE_OWN_GROWTH = 5


@dataclass
class Context:
    offset: int = 0  # bytes of the transcript read so far
    turns: int = 0
    start: int | None = None  # tokens in the first turn's context
    current: int = 0
    model: str | None = None
    last_message: str | None = None
    # Whether the watcher has told the orchestrator that this session is near its limit, which it does once.
    reported_limit: bool = False

    def growth(self) -> int:
        if self.start is None or self.turns < TURNS_BEFORE_OWN_GROWTH:
            return DEFAULT_GROWTH
        return max(1, round((self.current - self.start) / (self.turns - 1)))


def tokens_in(usage: dict) -> int:
    return (
        usage.get("input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
    )


def is_failed_request(entry: dict) -> bool:
    """Whether a transcript entry or an output event is Claude Code's own message for a failed request, such as one
    refused at a usage limit: it comes from the model "<synthetic>", with an error and zero usage."""
    return bool(entry.get("error")) or (entry.get("message") or {}).get("model") == "<synthetic>"


def update(context: Context, transcript: Path) -> Context:
    """Reads the turns that the transcript gained since `context` was last updated."""
    if not transcript.exists():
        return context
    if transcript.stat().st_size < context.offset:
        context = Context()
    with open(transcript, "rb") as file:
        file.seek(context.offset)
        data = file.read()
    complete = data[: data.rfind(b"\n") + 1]
    for line in complete.splitlines():
        entry = json.loads(line) if line.startswith(b"{") else {}
        message = entry.get("message") or {}
        if entry.get("type") != "assistant" or "usage" not in message or is_failed_request(entry):
            continue
        # A turn can span several entries that repeat its usage, so turns are counted by message.
        if message.get("id") != context.last_message:
            context.turns += 1
            context.last_message = message.get("id")
        context.current = tokens_in(message["usage"])
        context.start = context.current if context.start is None else context.start
        context.model = message.get("model", context.model)
    context.offset += len(complete)
    return context


def load(path: Path) -> dict[str, Context]:
    if not path.exists():
        return {}
    return {key: Context(**value) for key, value in json.loads(path.read_text()).items()}


def save(path: Path, contexts: dict[str, Context]) -> None:
    usage.write_atomically(path, json.dumps({key: asdict(value) for key, value in contexts.items()}))
