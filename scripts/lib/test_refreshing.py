import json
import tempfile
import unittest
from pathlib import Path

from refreshing import LONG_STATE, findings, tool_calls_since, turn_running

REQUEST_TIME = 1_792_540_000.0  # 2026-10-20T23:46:40Z


def entry(kind: str, timestamp: str, content: list[dict]) -> str:
    return json.dumps({"type": kind, "timestamp": timestamp, "message": {"content": content}}) + "\n"


class FindingsTest(unittest.TestCase):
    def test_a_short_state_with_open_references_is_fine(self):
        self.assertEqual(findings("# Goal\n\nShip the importer, waiting on #12.\n", closed={3}), [])

    def test_names_a_closed_issue_with_its_line(self):
        self.assertEqual(findings("# Goal\nWaiting on #12.\n", closed={12}), ["Line 2 names #12, which is closed."])

    def test_a_line_that_lists_several_issues(self):
        found = findings("In progress: #4, #5 and #6.\n", closed=set())
        self.assertEqual(
            found, ["Line 1 lists several issues or pull requests. Delete the ones that scripts/status lists."]
        )

    def test_two_issues_on_a_line_are_fine(self):
        self.assertEqual(findings("#4 waits for #5.\n", closed=set()), [])

    def test_a_repeated_heading(self):
        found = findings("## Waiting\n- a\n## Decisions\n## Waiting\n- b\n", closed=set())
        self.assertEqual(found, ['The heading "Waiting" appears 2 times. Merge them.'])

    def test_a_long_state_on_both_sides_of_the_limit(self):
        self.assertEqual(findings("a" * LONG_STATE, closed=set()), [])
        self.assertEqual(
            findings("a" * (LONG_STATE + 1), closed=set()),
            ["The state is longer than usual. Look for entries that an issue, a pull request or scripts/status holds."],
        )

    def test_a_colour_or_an_anchor_is_not_a_reference(self):
        self.assertEqual(findings("Colour &#123; and page#4 and #a1b2c3.\n", closed={4, 123}), [])


class ToolCallsSinceTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "transcript.jsonl"

    def write(self, *lines: str) -> None:
        self.path.write_text("".join(lines))

    def test_the_tool_call_that_made_the_request_doesnt_count(self):
        self.write(
            entry("assistant", "2026-10-20T23:46:39.500Z", [{"type": "tool_use", "name": "Bash"}]),
            entry("user", "2026-10-20T23:46:41.000Z", [{"type": "tool_result"}]),
            entry("assistant", "2026-10-20T23:46:43.000Z", [{"type": "text", "text": "Done."}]),
        )
        self.assertFalse(tool_calls_since(self.path, REQUEST_TIME))

    def test_a_tool_call_after_the_request_counts(self):
        self.write(entry("assistant", "2026-10-20T23:47:10.000Z", [{"type": "tool_use", "name": "Read"}]))
        self.assertTrue(tool_calls_since(self.path, REQUEST_TIME))

    def test_a_missing_transcript_has_no_tool_calls(self):
        self.assertFalse(tool_calls_since(self.path, REQUEST_TIME))


def prompt(timestamp: str, text: str) -> str:
    return json.dumps({"type": "user", "timestamp": timestamp, "message": {"role": "user", "content": text}}) + "\n"


def turn_end(timestamp: str) -> str:
    return json.dumps({"type": "system", "subtype": "turn_duration", "timestamp": timestamp}) + "\n"


class TurnRunningTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "transcript.jsonl"

    def test_a_prompt_after_the_last_turn_end_is_a_running_turn(self):
        self.path.write_text(turn_end("2026-10-21T07:06:19Z") + prompt("2026-10-21T07:06:36Z", "Write an essay."))
        self.assertTrue(turn_running(self.path))

    def test_a_turn_that_ended_is_not_running(self):
        self.path.write_text(prompt("2026-10-21T07:06:36Z", "Write an essay.") + turn_end("2026-10-21T07:06:58Z"))
        self.assertFalse(turn_running(self.path))

    def test_a_task_notification_starts_a_turn(self):
        self.path.write_text(turn_end("2026-10-21T07:06:19Z") + prompt("2026-10-21T07:06:20Z", "<task-notification>"))
        self.assertTrue(turn_running(self.path))

    def test_a_slash_command_starts_no_turn(self):
        self.path.write_text(turn_end("2026-10-21T07:04:39Z") + prompt("2026-10-21T07:04:40Z", "<command-name>/clear"))
        self.assertFalse(turn_running(self.path))

    def test_a_missing_transcript_runs_no_turn(self):
        self.assertFalse(turn_running(self.path))


if __name__ == "__main__":
    unittest.main()
