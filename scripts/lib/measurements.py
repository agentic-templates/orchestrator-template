"""What the loop measures about itself, such as how fast agents use an account, kept in .loop/measurements.jsonl.

Each measurement is stored under a key of what produced it, such as a model and an effort, never an agent type's
name. Changing an agent type's model or effort therefore stops its old measurements from counting, without anyone
clearing them. Only recent measurements count, so that a change on the provider's side ages out by itself.
"""

import json
import statistics
import time
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent.parent / ".loop" / "measurements.jsonl"
MAX_AGE_SECONDS = 7 * 24 * 3600
MAX_COUNT = 40


def record(key: str, value: float) -> None:
    PATH.parent.mkdir(exist_ok=True)
    with open(PATH, "a") as file:
        file.write(json.dumps({"key": key, "time": time.time(), "value": value}) + "\n")


def recent(key: str) -> list[float]:
    if not PATH.exists():
        return []
    cutoff = time.time() - MAX_AGE_SECONDS
    lines = [json.loads(line) for line in PATH.read_text().splitlines() if line]
    kept = [line for line in lines if line["time"] >= cutoff]
    if len(kept) < len(lines):
        PATH.write_text("".join(json.dumps(line) + "\n" for line in kept))
    return [line["value"] for line in kept if line["key"] == key][-MAX_COUNT:]


def median_or(key: str, default: float) -> float:
    values = recent(key)
    return statistics.median(values) if values else default
