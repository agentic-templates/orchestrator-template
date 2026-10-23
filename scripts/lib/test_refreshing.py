import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import refreshing
import usage
from refreshing import LONG_STATE, findings, last_turn_end, tool_calls_since, turn_running

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


def prompt(timestamp: str, content: str | list[dict]) -> str:
    return json.dumps({"type": "user", "timestamp": timestamp, "message": {"role": "user", "content": content}}) + "\n"


def meta(timestamp: str, text: str) -> str:
    """A prompt that Claude Code writes of its own, which it marks isMeta."""
    entry = {"type": "user", "isMeta": True, "timestamp": timestamp, "message": {"role": "user", "content": text}}
    return json.dumps(entry) + "\n"


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

    def test_a_message_claude_code_writes_of_its_own_can_start_a_turn(self):
        self.path.write_text(turn_end("2026-10-21T07:04:39Z") + meta("2026-10-21T07:04:40Z", "Stop hook feedback"))
        self.assertTrue(turn_running(self.path))

    def test_a_prompt_with_an_image_starts_a_turn(self):
        blocks = [{"type": "text", "text": "What's wrong here?"}, {"type": "image", "source": {}}]
        self.path.write_text(turn_end("2026-10-21T07:06:19Z") + prompt("2026-10-21T07:06:36Z", blocks))
        self.assertTrue(turn_running(self.path))

    def test_a_turn_that_has_written_a_lot_since_its_prompt_is_running(self):
        work = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 1000}]}}) + "\n"
        self.path.write_text(turn_end("2026-10-21T07:06:19Z") + prompt("2026-10-21T07:06:36Z", "Go.") + work * 3000)
        self.assertTrue(turn_running(self.path))

    def test_a_tool_result_starts_no_turn(self):
        blocks = [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]
        self.path.write_text(turn_end("2026-10-21T07:06:58Z") + prompt("2026-10-21T07:07:01Z", blocks))
        self.assertFalse(turn_running(self.path))

    def test_a_line_still_being_written_is_left_out(self):
        self.path.write_text(turn_end("2026-10-21T07:04:39Z") + '{"type": "user", "timest')
        self.assertFalse(turn_running(self.path))


class ClearTest(unittest.TestCase):
    def test_a_new_session_is_told_about_its_context_again(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        patches = [mock.patch.object(usage, "NOTICES", root / "notices")]
        names = ("REQUEST", "CHECKED", "WAITING", "FAILED")
        patches += [mock.patch.object(refreshing, name, root / name) for name in names]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        usage.notify(usage.ORCHESTRATOR_ID, "context", "Refresh soon.")
        self.assertEqual(usage.untold_notices(usage.ORCHESTRATOR_ID), ["Refresh soon."])
        refreshing.clear()
        usage.notify(usage.ORCHESTRATOR_ID, "context", "Refresh soon.")
        self.assertEqual(usage.untold_notices(usage.ORCHESTRATOR_ID), ["Refresh soon."])


class LastTurnEndTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "transcript.jsonl"

    def test_the_latest_turn_end(self):
        self.path.write_text(
            turn_end("2026-10-21T07:00:00Z") + prompt("2026-10-21T07:01:00Z", "Go.") + turn_end("2026-10-21T07:02:00Z")
        )
        self.assertEqual(last_turn_end(self.path), REQUEST_TIME + 26_120)  # 2026-10-21T07:02:00Z

    def test_lines_split_between_chunks_are_read_whole_and_in_order(self):
        lines = [turn_end(f"2026-10-21T07:{minute:02}:00Z") for minute in range(40)]
        self.path.write_text("".join(lines))
        with mock.patch.object(refreshing, "CHUNK_BYTES", 50):
            entries = list(refreshing.entries_backwards(self.path))
        self.assertEqual([json.dumps(entry) + "\n" for entry in reversed(entries)], lines)

    def test_no_turn_has_ended(self):
        self.path.write_text(prompt("2026-10-21T07:01:00Z", "Go."))
        self.assertIsNone(last_turn_end(self.path))


if __name__ == "__main__":
    unittest.main()
