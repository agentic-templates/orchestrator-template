import json
import subprocess
import sys

import contexts
import refreshing
import usage
import watching
from temporary_loop import Clock, TemporaryLoopTest

NOW = 1_792_540_000.0
REQUEST_TIME = NOW - 30


def box_screen(text: str) -> str:
    """Claude Code's screen with the text in its input box, or its dim placeholder for an empty box."""
    box = "❯\xa0" + (text or '\x1b[2mTry "fix lint errors"\x1b[0m')
    return "\n".join(["⏺ An earlier answer.", "", "─" * 40, box, "─" * 40, "  five hour 2% used"]) + "\n"


class SendClearTest(TemporaryLoopTest):
    def setUp(self):
        super().setUp()
        self.clock = Clock(NOW)
        self.clock.patch(self)
        self.send_clear = self.load_script("send-clear")

    def start_process(self, name: str) -> int:
        """A process whose command line names it as `name`, as a running script's does."""
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", name])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process.pid

    def test_does_nothing_while_another_sender_runs(self):
        pid = self.start_process("send-clear")
        (self.loop / "send-clear.pid").write_text(str(pid))
        self.assertEqual(self.run_script("send-clear"), (0, ""))
        self.assertFalse(refreshing.WAITING.exists())
        self.assertEqual((self.loop / "send-clear.pid").read_text(), str(pid))

    def test_stops_with_the_reason_when_the_orchestrator_has_no_tmux_pane(self):
        usage.ORCHESTRATOR.write_text(json.dumps({"transcript": "orchestrator.jsonl"}))
        code, _ = self.run_script("send-clear")
        self.assertEqual(code, 1)
        self.assertIn("the orchestrator doesn't run in tmux", refreshing.WAITING.read_text())

    def test_text_left_unchanged_for_two_minutes_is_abandoned(self):
        is_abandoned = self.send_clear["is_abandoned"]
        self.assertFalse(is_abandoned("Can you look at", REQUEST_TIME))
        self.clock.sleep(119)
        self.assertFalse(is_abandoned("Can you look at", REQUEST_TIME))
        self.clock.sleep(1)
        self.assertTrue(is_abandoned("Can you look at", REQUEST_TIME))

    def test_text_that_changed_counts_from_its_change(self):
        is_abandoned = self.send_clear["is_abandoned"]
        self.assertFalse(is_abandoned("Can you look at", REQUEST_TIME))
        self.clock.sleep(119)
        self.assertFalse(is_abandoned("Can you look at #12", REQUEST_TIME))
        self.clock.sleep(119)
        self.assertFalse(is_abandoned("Can you look at #12", REQUEST_TIME))

    def test_text_seen_at_an_earlier_refresh_counts_from_this_one(self):
        is_abandoned = self.send_clear["is_abandoned"]
        self.assertFalse(is_abandoned("ok", REQUEST_TIME))
        self.clock.sleep(3 * 3600)
        self.assertFalse(is_abandoned("ok", NOW + 3 * 3600 - 30))

    def test_goes_on_once_the_editor_took_the_text(self):
        self.send_clear["wait_for_editor"]()
        self.assertEqual(self.clock.now, NOW)

    def test_the_wait_for_the_editor_ends_at_its_deadline(self):
        (self.loop / "taking-input").write_text("")
        self.send_clear["wait_for_editor"]()
        self.assertGreaterEqual(self.clock.now, NOW + self.send_clear["EDITOR_SECONDS"])

    def show_in_tmux(self, screen: str, after_backspaces: str | None = None) -> None:
        """A stand-in for tmux, which shows the screen until a Backspace changes it, and opens no editor."""
        (self.root / "screen").write_text(screen)
        if after_backspaces is not None:
            (self.root / "after-backspaces").write_text(after_backspaces)
        self.stand_in(
            "tmux",
            'echo "$@" >> tmux-calls\n'
            'case "$*" in\n'
            "  capture-pane*) cat screen ;;\n"
            "  *BSpace*) [ -f after-backspaces ] && cp after-backspaces screen ;;\n"
            "esac\n"
            "exit 0",
        )

    def test_text_that_no_editor_takes_is_deleted(self):
        self.show_in_tmux(box_screen("half a thought"), after_backspaces=box_screen(""))
        typed = self.loop / "refresh-typed.json"
        typed.write_text("{}")
        self.send_clear["take_text"]("%3")
        calls = (self.root / "tmux-calls").read_text()
        self.assertIn("send-keys -t %3 C-g", calls)
        self.assertIn("send-keys -t %3 -N 2000 BSpace", calls)
        self.assertFalse((self.loop / "taking-input").exists())
        self.assertFalse(typed.exists())

    def test_stops_with_the_reason_when_the_input_box_stays_hidden(self):
        dialog = "Do you want to proceed?\n❯ 1. Yes\n  2. No\n"
        self.show_in_tmux(dialog)
        with self.assertRaisesRegex(self.send_clear["CantSend"], "If an editor or a dialog shows"):
            self.send_clear["take_text"]("%3")
        self.assertFalse((self.loop / "taking-input").exists())

    def orchestrate(self, current: int, wrote_minutes_ago: float) -> None:
        """An orchestrator that started at 30,000 tokens on a window it hasn't reported, which is 1M unless the
        account recorded another, with the time the maintainer last wrote."""
        context = contexts.Context(turns=2, start=30_000, current=current, model="claude-opus-5-5")
        contexts.save(watching.CONTEXTS, {"orchestrator.jsonl": context})
        usage.ORCHESTRATOR.write_text(json.dumps({"account": "claude", "transcript": "orchestrator.jsonl"}))
        usage.MAINTAINER.write_text(json.dumps({"last_message": NOW - wrote_minutes_ago * 60}))

    def test_waits_while_the_maintainer_wrote_in_the_last_five_minutes(self):
        self.orchestrate(current=100_000, wrote_minutes_ago=4.9)
        self.assertTrue(self.send_clear["should_wait_for_maintainer"]())

    def test_doesnt_wait_once_five_minutes_passed_since_the_maintainer_wrote(self):
        self.orchestrate(current=100_000, wrote_minutes_ago=5)
        self.assertFalse(self.send_clear["should_wait_for_maintainer"]())

    def test_doesnt_wait_for_the_maintainer_once_the_context_is_near_its_limit(self):
        # Past 80% of the 1M window, where everything winds down, so past the point that waits for the maintainer.
        self.orchestrate(current=850_000, wrote_minutes_ago=1)
        self.assertFalse(self.send_clear["should_wait_for_maintainer"]())

    def test_the_window_recorded_for_the_orchestrators_account_and_model_counts(self):
        usage.record_context_windows("claude", {"claude-opus-5-5": {"contextWindow": 200_000}})
        # Past 80% of the 200k window, where the watcher already winds everything down.
        self.orchestrate(current=170_000, wrote_minutes_ago=1)
        self.assertFalse(self.send_clear["should_wait_for_maintainer"]())
