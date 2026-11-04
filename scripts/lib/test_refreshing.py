import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import refreshing
import usage
from refreshing import LONG_STATE, called_a_tool_since, findings, last_turn_end, turn_running
from temporary_loop import TemporaryLoopTest

REQUEST_TIME = 1_792_540_000.0  # 2026-10-20T23:46:40Z


def entry(kind: str, timestamp: str, content: list[dict]) -> str:
    return json.dumps({"type": kind, "timestamp": timestamp, "message": {"content": content}}) + "\n"


class FindingsTest(unittest.TestCase):
    def test_a_short_state_with_open_references_is_fine(self):
        self.assertEqual(findings("# Goal\n\nShip the importer, waiting on #12.\n", closed={3}), [])

    def test_names_a_closed_issue_with_its_line(self):
        self.assertEqual(findings("# Goal\nWaiting on #12.\n", closed={12}), ["Line 2 names #12, which is closed."])

    def test_a_line_that_lists_three_issues_is_reported(self):
        found = findings("In progress: #4, #5 and #6.\n", closed=set())
        self.assertEqual(
            found, ["Line 1 lists several issues or pull requests. Delete the ones that scripts/status lists."]
        )

    def test_a_line_that_lists_four_issues_is_reported(self):
        self.assertEqual(
            findings("In progress: #4, #5, #6 and #7.\n", closed=set()),
            ["Line 1 lists several issues or pull requests. Delete the ones that scripts/status lists."],
        )

    def test_two_issues_on_a_line_are_fine(self):
        self.assertEqual(findings("#4 waits for #5.\n", closed=set()), [])

    def test_a_repeated_heading_is_reported(self):
        found = findings("## Waiting\n- a\n## Decisions\n## Waiting\n- b\n", closed=set())
        self.assertEqual(found, ['The heading "Waiting" appears 2 times. Merge them.'])

    def test_a_state_is_reported_once_it_is_over_the_limit(self):
        self.assertEqual(findings("a" * LONG_STATE, closed=set()), [])
        reported = (
            f"The state has {LONG_STATE + 1:,} characters, over {LONG_STATE:,}. Look for entries that an issue or a "
            "pull request records, or that scripts/status lists."
        )
        self.assertEqual(findings("a" * (LONG_STATE + 1), closed=set()), [reported])

    def test_a_colour_or_an_anchor_is_not_a_reference(self):
        self.assertEqual(findings("Colour &#123; and page#4 and #a1b2c3.\n", closed={4, 123}), [])


class CalledAToolSinceTest(unittest.TestCase):
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
        self.assertFalse(called_a_tool_since(self.path, REQUEST_TIME))

    def test_a_tool_call_after_the_request_counts(self):
        self.write(entry("assistant", "2026-10-20T23:47:10.000Z", [{"type": "tool_use", "name": "Read"}]))
        self.assertTrue(called_a_tool_since(self.path, REQUEST_TIME))

    def test_a_missing_transcript_has_no_tool_calls(self):
        self.assertFalse(called_a_tool_since(self.path, REQUEST_TIME))

    def test_a_tool_call_followed_by_its_result_counts(self):
        self.write(
            entry("assistant", "2026-10-20T23:47:10.000Z", [{"type": "tool_use", "name": "Read"}]),
            entry("user", "2026-10-20T23:47:11.000Z", [{"type": "tool_result"}]),
        )
        self.assertTrue(called_a_tool_since(self.path, REQUEST_TIME))


class IsCancelledTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "transcript.jsonl"

    def test_a_request_without_a_transcript_is_never_cancelled(self):
        self.assertFalse(refreshing.is_cancelled({"transcript": None, "time": REQUEST_TIME}))

    def test_only_a_tool_call_after_the_request_cancels_it(self):
        self.path.write_text(entry("assistant", "2026-10-20T23:46:39.000Z", [{"type": "tool_use", "name": "Bash"}]))
        self.assertFalse(refreshing.is_cancelled({"transcript": str(self.path), "time": REQUEST_TIME}))
        self.path.write_text(entry("assistant", "2026-10-20T23:47:10.000Z", [{"type": "tool_use", "name": "Bash"}]))
        self.assertTrue(refreshing.is_cancelled({"transcript": str(self.path), "time": REQUEST_TIME}))


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

    def test_a_commands_output_starts_no_turn(self):
        output = "<local-command-stdout>Context usage: 41k tokens</local-command-stdout>"
        self.path.write_text(turn_end("2026-10-21T07:04:39Z") + prompt("2026-10-21T07:04:41Z", output))
        self.assertFalse(turn_running(self.path))

    def test_an_api_error_in_a_turn_doesnt_end_it(self):
        api_error = json.dumps({"type": "system", "subtype": "api_error", "timestamp": "2026-10-21T07:02:00Z"}) + "\n"
        self.path.write_text(turn_end("2026-10-21T07:00:00Z") + prompt("2026-10-21T07:01:00Z", "Go.") + api_error)
        self.assertTrue(turn_running(self.path))
        self.assertEqual(last_turn_end(self.path), REQUEST_TIME + 26_000)  # 2026-10-21T07:00:00Z

    def test_a_missing_transcript_runs_no_turn(self):
        self.assertFalse(turn_running(self.path))

    def test_a_message_claude_code_writes_of_its_own_can_start_a_turn(self):
        self.path.write_text(turn_end("2026-10-21T07:04:39Z") + meta("2026-10-21T07:04:40Z", "Stop hook feedback"))
        self.assertTrue(turn_running(self.path))

    def test_a_prompt_with_an_image_starts_a_turn(self):
        blocks = [{"type": "text", "text": "What's wrong here?"}, {"type": "image", "source": {}}]
        self.path.write_text(turn_end("2026-10-21T07:06:19Z") + prompt("2026-10-21T07:06:36Z", blocks))
        self.assertTrue(turn_running(self.path))

    def test_a_turn_that_has_written_more_than_a_chunk_since_its_prompt_is_running(self):
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


class ForgetLastSessionTest(TemporaryLoopTest):
    def test_a_new_session_is_told_about_its_context_again(self):
        usage.notify(usage.ORCHESTRATOR_ID, "context", "Refresh soon.")
        self.assertEqual(usage.untold_notices(usage.ORCHESTRATOR_ID), ["Refresh soon."])
        refreshing.forget_last_session()
        usage.notify(usage.ORCHESTRATOR_ID, "context", "Refresh soon.")
        self.assertEqual(usage.untold_notices(usage.ORCHESTRATOR_ID), ["Refresh soon."])

    def test_a_new_session_drops_the_request_the_shown_checklist_and_a_failed_refresh(self):
        for path in (refreshing.REQUEST, refreshing.CHECKED, refreshing.WAITING, refreshing.FAILED):
            path.write_text("")
        refreshing.forget_last_session()
        self.assertEqual(list(self.loop.iterdir()), [])


class RequestTest(TemporaryLoopTest):
    def logged_sizes(self) -> list[int]:
        return [json.loads(line)["state_characters"] for line in refreshing.LOG.read_text().splitlines()]

    def test_counts_long_states_in_a_row_and_logs_each_size(self):
        refreshing.STATE.write_text("a" * 7_000)
        counts = []
        for transcript in ("1.jsonl", "2.jsonl", "3.jsonl"):
            refreshing.request(transcript)
            counts.append(refreshing.LONG_STATES.read_text())
        refreshing.STATE.write_text("a" * 100)
        refreshing.request("4.jsonl")
        counts.append(refreshing.LONG_STATES.read_text())
        self.assertEqual(counts, ["1", "2", "3", "0"])
        self.assertEqual(self.logged_sizes(), [7_000, 7_000, 7_000, 100])

    def test_a_session_that_requests_again_after_a_cancel_counts_once(self):
        refreshing.STATE.write_text("a" * 7_000)
        refreshing.request("1.jsonl")
        refreshing.request("1.jsonl")
        self.assertEqual(refreshing.LONG_STATES.read_text(), "1")
        self.assertEqual(self.logged_sizes(), [7_000])

    def test_requests_from_an_unknown_transcript_count_each_time(self):
        refreshing.STATE.write_text("a" * 7_000)
        refreshing.request(None)
        refreshing.request(None)
        self.assertEqual(refreshing.LONG_STATES.read_text(), "2")
        self.assertEqual(self.logged_sizes(), [7_000, 7_000])

    def test_a_missing_state_is_not_long(self):
        refreshing.request(None)
        self.assertEqual(refreshing.LONG_STATES.read_text(), "0")
        self.assertEqual(self.logged_sizes(), [0])

    def test_the_request_is_read_back(self):
        self.assertIsNone(refreshing.load_request())
        refreshing.request("t.jsonl")
        self.assertEqual((refreshing.load_request() or {}).get("transcript"), "t.jsonl")


class LastTurnEndTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "transcript.jsonl"

    def test_gives_the_time_of_the_latest_turn_end(self):
        self.path.write_text(
            turn_end("2026-10-21T07:00:00Z") + prompt("2026-10-21T07:01:00Z", "Go.") + turn_end("2026-10-21T07:02:00Z")
        )
        self.assertEqual(last_turn_end(self.path), REQUEST_TIME + 26_120)  # 2026-10-21T07:02:00Z

    def test_gives_none_before_any_turn_has_ended(self):
        self.path.write_text(prompt("2026-10-21T07:01:00Z", "Go."))
        self.assertIsNone(last_turn_end(self.path))


class EntriesBackwardsTest(unittest.TestCase):
    def test_lines_split_between_chunks_are_read_whole_from_last_to_first(self):
        path = Path(tempfile.mkdtemp()) / "transcript.jsonl"
        lines = [turn_end(f"2026-10-21T07:{minute:02}:00Z") for minute in range(40)]
        path.write_text("".join(lines))
        with mock.patch.object(refreshing, "CHUNK_BYTES", 50):
            entries = list(refreshing.entries_backwards(path))
        self.assertEqual([json.dumps(entry) + "\n" for entry in reversed(entries)], lines)


if __name__ == "__main__":
    unittest.main()
