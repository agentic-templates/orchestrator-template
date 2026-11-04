import json
import subprocess
import sys

import contexts
import refreshing
import usage
import watching
from temporary_loop import Clock, TemporaryLoopTest

NOW = 1_792_540_000.0


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
        self.assertFalse(is_abandoned("Can you look at"))
        self.clock.sleep(119)
        self.assertFalse(is_abandoned("Can you look at"))
        self.clock.sleep(1)
        self.assertTrue(is_abandoned("Can you look at"))

    def test_text_that_changed_counts_from_its_change(self):
        is_abandoned = self.send_clear["is_abandoned"]
        self.assertFalse(is_abandoned("Can you look at"))
        self.clock.sleep(119)
        self.assertFalse(is_abandoned("Can you look at #12"))
        self.clock.sleep(119)
        self.assertFalse(is_abandoned("Can you look at #12"))

    def orchestrate(self, current: int, wrote_minutes_ago: float) -> None:
        """An orchestrator on a 1M window that started at 30,000 tokens, with the time the maintainer last wrote."""
        context = contexts.Context(turns=2, start=30_000, current=current, model="claude-opus-5-5")
        contexts.save(watching.CONTEXTS, {"orchestrator.jsonl": context})
        usage.ORCHESTRATOR.write_text(json.dumps({"account": "claude", "transcript": "orchestrator.jsonl"}))
        usage.MAINTAINER.write_text(json.dumps({"last_message": NOW - wrote_minutes_ago * 60}))

    def test_waits_while_the_maintainer_wrote_in_the_last_five_minutes(self):
        self.orchestrate(current=100_000, wrote_minutes_ago=4.9)
        self.assertTrue(self.send_clear["maintainer_is_writing"]())

    def test_doesnt_wait_once_five_minutes_passed_since_the_maintainer_wrote(self):
        self.orchestrate(current=100_000, wrote_minutes_ago=5)
        self.assertFalse(self.send_clear["maintainer_is_writing"]())

    def test_doesnt_wait_for_the_maintainer_once_the_context_is_near_its_limit(self):
        # Past 80% of the 1M window, where everything winds down, so past the point that waits for the maintainer.
        self.orchestrate(current=850_000, wrote_minutes_ago=1)
        self.assertFalse(self.send_clear["maintainer_is_writing"]())
