import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import meter
import usage
import watching


def decision(action: str) -> meter.Decision:
    return meter.Decision(action, 0, 0, 40.0, 0.1)


def turn(message_id: str, tokens: int) -> str:
    usage_counts = {"input_tokens": 10, "cache_read_input_tokens": tokens - 10, "cache_creation_input_tokens": 0}
    entry = {"type": "assistant", "message": {"id": message_id, "model": "claude-opus-5-5", "usage": usage_counts}}
    return json.dumps(entry) + "\n"


def turn_end(ended: float) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended))
    return json.dumps({"type": "system", "subtype": "turn_duration", "timestamp": stamp}) + "\n"


class TempLoopTest(unittest.TestCase):
    """Points the loop's files at a fresh folder, with one account, claude-2, whose home is in it too."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for module, name, path in [
            (usage, "ACCOUNTS", self.root / "accounts.json"),
            (usage, "AGENTS", self.root / "agents"),
            (usage, "NOTICES", self.root / "notices"),
            (usage, "ORCHESTRATOR", self.root / "orchestrator.json"),
            (usage, "CONTEXT_WINDOWS", self.root / "context-windows.json"),
            (watching, "CONTEXTS", self.root / "contexts.json"),
        ]:
            patch = mock.patch.object(module, name, path)
            patch.start()
            self.addCleanup(patch.stop)
        self.home = self.root / "claude-2-home"
        accounts = [{"name": "claude-2", "provider": "claude", "home": str(self.home)}]
        (self.root / "accounts.json").write_text(json.dumps(accounts))

    def start_agent(self, started: float) -> dict:
        """An agent's record, with a process that counts as its running launcher."""
        launcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "launch"])
        self.addCleanup(launcher.wait)
        self.addCleanup(launcher.kill)
        agent = {"id": "builder-1-a", "name": "builder", "account": "claude-2", "session": "s1", "pid": launcher.pid}
        usage.save_agent(agent | {"started": started})
        return agent

    def agent_transcript(self) -> Path:
        # Claude Code shortens long folder names, so the folder doesn't follow from the worktree's path.
        folder = self.home / "projects" / "-Users-x-dev-my-project--loop-worktrees-builder-1-a-1f3e9a"
        folder.mkdir(parents=True)
        return folder / "s1.jsonl"


class MeterChangesTest(unittest.TestCase):
    def test_the_first_check_only_notes_the_decisions(self):
        state = {}
        self.assertEqual(watching.meter_changes(state, {"claude": decision("start")}), [])
        self.assertEqual(state["actions"], {"claude": "start"})

    def test_a_change_since_the_watcher_before_is_reported(self):
        state = {"actions": {"claude": "start", "claude-2": "hold"}}
        events = watching.meter_changes(state, {"claude": decision("pause"), "claude-2": decision("hold")})
        self.assertEqual(events, ["Usage: claude now says pause."])


class CheckPullRequestsTest(unittest.TestCase):
    """gh stands in for GitHub."""

    def listing(self, count: int):
        def gh_json(*args: str) -> list[dict]:
            # gh lists 30 pull requests unless it's given a limit.
            limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 30
            return [{"number": number, "statusCheckRollup": []} for number in range(1, count + 1)][:limit]

        return gh_json

    def test_more_than_30_open_pull_requests_dont_count_as_merged(self):
        state = {"pull_requests": {str(number): "passing" for number in range(1, 41)}}
        with mock.patch.object(watching.github, "gh_json", self.listing(40)):
            self.assertEqual(watching.check_pull_requests(state), [])

    def test_a_github_failure_is_reported_once_and_the_last_list_kept(self):
        state = {"pull_requests": {"7": "passing"}}

        def failing(*args: str) -> None:
            raise SystemExit("gh pr list failed: HTTP 401: Bad credentials")

        with mock.patch.object(watching.github, "gh_json", failing):
            events = watching.check_pull_requests(state)
            self.assertEqual(len(events), 1)
            self.assertIn("HTTP 401: Bad credentials", events[0])
            self.assertEqual(watching.check_pull_requests(state), [])
        with mock.patch.object(watching.github, "gh_json", self.listing(0)):
            self.assertEqual(watching.check_pull_requests(state), ["#7 merged or closed."])
        with mock.patch.object(watching.github, "gh_json", failing):
            self.assertEqual(len(watching.check_pull_requests(state)), 1)


class CheckContextsTest(TempLoopTest):
    def orchestrate(self, transcript_text: str) -> None:
        transcript = self.root / "orchestrator.jsonl"
        transcript.write_text(transcript_text)
        usage.ORCHESTRATOR.write_text(json.dumps({"account": "claude-2", "transcript": str(transcript)}))

    def test_the_orchestrator_near_its_limit_is_reported_once(self):
        # 1M window, so the point near the limit is 800,000 tokens.
        self.orchestrate(turn("a", 40_000) + turn("b", 850_000))
        self.assertEqual(len(watching.check_contexts()), 1)
        self.orchestrate(turn("a", 40_000) + turn("b", 850_000) + turn("c", 860_000))
        self.assertEqual(watching.check_contexts(), [])

    def test_a_window_recorded_for_the_account_and_model_is_used(self):
        usage.record_context_windows("claude-2", {"claude-opus-5-5": {"contextWindow": 200_000}})
        # 200k window, so the point near the limit is 160,000 tokens.
        self.orchestrate(turn("a", 40_000) + turn("b", 170_000))
        self.assertEqual(len(watching.check_contexts()), 1)

    def test_an_agent_near_its_limit_is_told_to_hand_off_now_without_waking_the_orchestrator(self):
        self.start_agent(time.time())
        self.agent_transcript().write_text(turn("a", 40_000) + turn("b", 850_000))
        self.assertEqual(watching.check_contexts(), [])
        self.assertEqual(usage.untold_notices("builder-1-a"), [watching.HAND_OFF_NOW])


class WarmWakeTest(TempLoopTest):
    def orchestrate(self, ended: float) -> None:
        transcript = self.root / "orchestrator.jsonl"
        transcript.write_text(turn_end(ended))
        usage.ORCHESTRATOR.write_text(json.dumps({"account": "claude-2", "transcript": str(transcript)}))

    def test_counts_from_the_orchestrators_last_turn_after_the_watcher_started(self):
        ended = time.time() - 600
        self.orchestrate(ended)
        self.assertAlmostEqual(watching.warm_wake_at(ended - 60), ended + 50 * 60, delta=1)

    def test_counts_from_the_watchers_start_while_the_turn_that_started_it_runs(self):
        started = time.time()
        self.orchestrate(started - 50 * 60 - 10)
        self.assertEqual(watching.warm_wake_at(started), started + 50 * 60)

    def test_counts_from_the_watchers_start_before_any_turn(self):
        self.assertEqual(watching.warm_wake_at(1000.0), 1000.0 + 50 * 60)


class CheckStalledTest(TempLoopTest):
    def test_an_agent_with_no_transcript_long_after_its_start_is_reported(self):
        self.start_agent(time.time() - 31 * 60)
        self.assertEqual(len(watching.check_stalled({})), 1)

    def test_an_agent_that_just_started_without_a_transcript_isnt(self):
        self.start_agent(time.time())
        self.assertEqual(watching.check_stalled({}), [])


if __name__ == "__main__":
    unittest.main()
