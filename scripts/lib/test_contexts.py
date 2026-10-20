import json
import tempfile
import unittest
from pathlib import Path

from contexts import DEFAULT_GROWTH, Context, update


def turn(message_id: str, tokens: int, model: str = "claude-opus-5-5") -> str:
    usage = {"input_tokens": 10, "cache_read_input_tokens": tokens - 10, "cache_creation_input_tokens": 0}
    return json.dumps({"type": "assistant", "message": {"id": message_id, "model": model, "usage": usage}}) + "\n"


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "transcript.jsonl"

    def write(self, *lines: str) -> None:
        with open(self.path, "a") as file:
            file.write("".join(lines))

    def test_reads_the_start_the_current_size_and_the_model(self):
        self.write(json.dumps({"type": "user"}) + "\n", turn("a", 40_000), turn("b", 44_000))
        context = update(Context(), self.path)
        self.assertEqual((context.start, context.current, context.turns), (40_000, 44_000, 2))
        self.assertEqual(context.model, "claude-opus-5-5")

    def test_counts_a_turn_once_when_its_usage_repeats(self):
        self.write(turn("a", 40_000), turn("a", 40_000), turn("b", 45_000))
        self.assertEqual(update(Context(), self.path).turns, 2)

    def test_reads_only_what_the_transcript_gained_and_waits_for_a_whole_line(self):
        self.write(turn("a", 40_000))
        context = update(Context(), self.path)
        self.write(turn("b", 50_000)[:20])
        context = update(context, self.path)
        self.assertEqual(context.turns, 1)
        self.write(turn("b", 50_000)[20:])
        self.assertEqual(update(context, self.path).current, 50_000)

    def test_measures_growth_once_a_session_has_had_enough_turns(self):
        self.write(turn("a", 40_000))
        self.assertEqual(update(Context(), self.path).growth(), DEFAULT_GROWTH)
        self.write(*(turn(str(i), 40_000 + i * 2_000) for i in range(1, 10)))
        self.assertEqual(update(Context(), self.path).growth(), 2_000)


if __name__ == "__main__":
    unittest.main()
