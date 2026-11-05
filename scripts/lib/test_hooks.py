import io
import json
import os
from contextlib import redirect_stdout

import refreshing
import usage
from temporary_loop import TemporaryLoopTest

REQUEST_TIME = 1_792_540_000.0  # 2026-10-20T23:46:40Z


class HookTest(TemporaryLoopTest):
    def orchestrate_elsewhere(self) -> int:
        """Records an orchestrator that runs in another session of the checkout."""
        pid = self.start_process("claude")
        usage.ORCHESTRATOR.write_text(json.dumps({"account": "claude", "pid": pid, "started": usage.started_at(pid)}))
        return pid


class AutoMergeTest(HookTest):
    URL = "https://github.com/owner/repo/pull/7"

    def setUp(self):
        super().setUp()
        self.stand_in("gh", 'echo "$@" >> gh-calls')
        self.calls = self.root / "gh-calls"

    def open_pull_request(self, command: str) -> str:
        event = {"tool_input": {"command": command}, "tool_response": {"stdout": self.URL + "\n"}}
        return self.run_script("hooks/auto-merge", stdin=json.dumps(event))[1]

    def test_turns_on_squashed_auto_merge_for_the_new_pull_request(self):
        output = self.open_pull_request("gh pr create --title 'fix: x' --body-file body.md")
        self.assertEqual(self.calls.read_text(), f"pr merge --auto --squash {self.URL}\n")
        self.assertIn(f"Auto-merge is on for {self.URL}.", output)

    def test_tells_the_builder_that_its_pull_request_wont_merge_when_auto_merge_fails(self):
        self.stand_in("gh", 'echo "GraphQL: Pull request Auto merge is not allowed for this repository" >&2\nexit 1')
        output = self.open_pull_request("gh pr create --title 'fix: x' --body-file body.md")
        self.assertIn(
            f"Turning on auto-merge for {self.URL} failed, so the pull request won't merge by itself. gh's error: "
            "GraphQL: Pull request Auto merge is not allowed for this repository",
            output,
        )

    def test_leaves_a_draft_alone(self):
        self.assertEqual(self.open_pull_request("gh pr create --draft --title 'fix: x' --body-file body.md"), "")
        self.assertFalse(self.calls.exists())

    def test_does_nothing_for_a_command_that_opened_no_pull_request(self):
        event = {"tool_input": {"command": "gh pr view 7"}, "tool_response": {"stdout": "title: fix: x\n"}}
        self.assertEqual(self.run_script("hooks/auto-merge", stdin=json.dumps(event)), (0, ""))
        self.assertFalse(self.calls.exists())


class MaintainerTest(HookTest):
    def test_records_when_the_maintainer_wrote(self):
        self.run_script("hooks/maintainer", stdin=json.dumps({"prompt": "Start on #12."}))
        self.assertTrue(usage.MAINTAINER.exists())

    def test_a_prompt_that_starts_with_a_tag_isnt_the_maintainer(self):
        prompt = "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>"
        self.run_script("hooks/maintainer", stdin=json.dumps({"prompt": prompt}))
        self.assertFalse(usage.MAINTAINER.exists())

    def test_a_second_session_in_the_checkout_isnt_the_maintainer_writing_to_the_orchestrator(self):
        self.orchestrate_elsewhere()
        self.run_script("hooks/maintainer", stdin=json.dumps({"prompt": "Start on #12."}))
        self.assertFalse(usage.MAINTAINER.exists())


class NoticesTest(HookTest):
    def notices(self, event: dict) -> str:
        return self.run_script("hooks/notices", stdin=json.dumps(event))[1]

    def test_shows_the_orchestrator_its_notice_once(self):
        usage.notify(usage.ORCHESTRATOR_ID, "context", "Refresh soon.")
        self.assertIn("Refresh soon.", self.notices({}))
        self.assertEqual(self.notices({}), "")

    def test_shows_a_launched_agent_its_own_notice(self):
        usage.notify(usage.ORCHESTRATOR_ID, "context", "Refresh soon.")
        usage.notify("builder-7", "wind-down", "Wind down now.")
        os.environ["LOOP_AGENT_ID"] = "builder-7"
        output = self.notices({})
        self.assertIn("Wind down now.", output)
        self.assertNotIn("Refresh soon.", output)

    def test_a_launched_agents_subagent_leaves_the_agents_notice_for_it(self):
        usage.notify("builder-7", "wind-down", "Wind down now.")
        os.environ["LOOP_AGENT_ID"] = "builder-7"
        self.assertEqual(self.notices({"agent_id": "a1b2c3", "agent_type": "Explore"}), "")
        self.assertEqual(usage.untold_notices("builder-7"), ["Wind down now."])

    def test_the_orchestrators_subagent_leaves_the_orchestrators_notice_for_it(self):
        usage.notify(usage.ORCHESTRATOR_ID, "context", "Refresh soon.")
        self.assertEqual(self.notices({"agent_id": "a1b2c3", "agent_type": "general-purpose"}), "")
        self.assertEqual(usage.untold_notices(usage.ORCHESTRATOR_ID), ["Refresh soon."])

    def test_a_second_session_in_the_checkout_doesnt_take_the_orchestrators_notices(self):
        self.orchestrate_elsewhere()
        usage.notify(usage.ORCHESTRATOR_ID, "context", "Refresh soon.")
        self.assertEqual(self.notices({}), "")
        self.assertEqual(usage.untold_notices(usage.ORCHESTRATOR_ID), ["Refresh soon."])


class RefreshHookTest(HookTest):
    def setUp(self):
        super().setUp()
        # A request that a tool call after it cancelled, so that the hook never starts scripts/send-clear.
        transcript = self.root / "orchestrator.jsonl"
        call = {
            "type": "assistant",
            "timestamp": "2026-10-20T23:47:10Z",
            "message": {"content": [{"type": "tool_use"}]},
        }
        transcript.write_text(json.dumps(call) + "\n")
        refreshing.REQUEST.write_text(json.dumps({"time": REQUEST_TIME, "transcript": str(transcript)}))

    def stop(self, event: dict) -> str:
        return self.run_script("hooks/refresh", stdin=json.dumps(event))[1]

    def test_a_tool_call_after_the_request_cancels_it_and_asks_for_another(self):
        output = json.loads(self.stop({"hook_event_name": "Stop"}))
        self.assertEqual(output["decision"], "block")
        self.assertIn("run scripts/refresh --ready again", output["reason"])
        self.assertFalse(refreshing.REQUEST.exists())

    def test_a_launched_agents_stop_leaves_the_orchestrators_request_alone(self):
        self.assertEqual(self.stop({"hook_event_name": "Stop", "agent_type": "builder"}), "")
        self.assertTrue(refreshing.REQUEST.exists())

    def test_a_second_sessions_stop_leaves_the_orchestrators_request_alone(self):
        self.orchestrate_elsewhere()
        self.assertEqual(self.stop({"hook_event_name": "Stop"}), "")
        self.assertTrue(refreshing.REQUEST.exists())


class SessionStartTest(HookTest):
    def start(self, event: dict, *arguments: str) -> str:
        return self.run_script("hooks/session-start", *arguments, stdin=json.dumps(event))[1]

    def test_the_orchestrators_new_session_records_its_pane_and_drops_the_old_request(self):
        os.environ["TMUX_PANE"] = "%3"
        refreshing.REQUEST.write_text("{}")
        refreshing.FAILED.write_text("")
        output = json.loads(self.start({"source": "clear"}, "skills", "claude"))
        self.assertIn("# Orchestrator", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(usage.orchestrator()["pane"], "%3")
        self.assertFalse(refreshing.REQUEST.exists())
        self.assertFalse(refreshing.FAILED.exists())

    def test_a_launched_agents_session_leaves_the_orchestrators_records_alone(self):
        refreshing.REQUEST.write_text("{}")
        self.assertEqual(self.start({"source": "startup", "agent_type": "builder"}, "skills", "claude"), "")
        self.assertFalse(usage.ORCHESTRATOR.exists())
        self.assertTrue(refreshing.REQUEST.exists())

    def test_a_second_session_in_the_checkout_is_told_that_it_isnt_the_orchestrator(self):
        pid = self.orchestrate_elsewhere()
        refreshing.REQUEST.write_text("{}")
        output = json.loads(self.start({"source": "startup"}, "skills", "claude"))
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"An orchestrator already runs in this checkout, as process {pid}", context)
        self.assertTrue(refreshing.REQUEST.exists())
        self.assertEqual(self.start({"source": "startup"}, "state"), "")


class SessionStartStateTest(HookTest):
    CUT = "\n\n[This is the first part of .loop/state.md. Read the rest of the file.]"

    def state(self) -> str:
        output = json.loads(self.run_script("hooks/session-start", "state", stdin='{"source": "clear"}')[1])
        return output["hookSpecificOutput"]["additionalContext"]

    def test_says_when_there_is_no_saved_state(self):
        self.assertEqual(self.state(), "No saved state: .loop/state.md doesn't exist.")

    def test_a_state_that_fits_is_given_whole(self):
        refreshing.STATE.write_text("# Goal\nShip the importer.\n")
        self.assertEqual(self.state(), "# Your saved state (.loop/state.md)\n\n# Goal\nShip the importer.")

    def test_a_state_over_the_limit_is_cut_with_where_to_read_the_rest(self):
        refreshing.STATE.write_text("a" * 15_000)
        context = self.state()
        self.assertEqual(len(context), 10_000)
        self.assertTrue(context.endswith(self.CUT))

    def test_the_second_long_state_in_a_row_adds_the_note_and_keeps_where_to_read_the_rest(self):
        refreshing.STATE.write_text("a" * 15_000)
        refreshing.LONG_STATES.write_text("2")
        context = self.state()
        self.assertTrue(
            context.startswith(
                "Send the maintainer this notification: \"The orchestrator's saved state was long at each of the last 2 "
                "refreshes."
            )
        )
        self.assertLessEqual(len(context), 10_000)
        self.assertTrue(context.endswith(self.CUT))
        self.assertFalse(refreshing.LONG_STATES.exists())

    def test_the_first_long_state_adds_no_note(self):
        refreshing.STATE.write_text("a" * 7_000)
        refreshing.LONG_STATES.write_text("1")
        self.assertTrue(self.state().startswith("# Your saved state"))
        self.assertEqual(refreshing.LONG_STATES.read_text(), "1")


class SessionStartStatusTest(HookTest):
    def test_a_failed_status_is_marked_as_failed(self):
        status_context = self.load_script("hooks/session-start")["status_context"]
        context = status_context(1, "## Running agents\nNone\n", "Traceback (most recent call last):\n")
        self.assertTrue(context.startswith("# scripts/status --save failed with exit status 1\n\n"))
        self.assertIn("Run scripts/status before you start or stop agents.", context)
        self.assertIn("## Running agents\nNone\nTraceback", context)

    def test_the_status_is_given_under_its_heading(self):
        status_context = self.load_script("hooks/session-start")["status_context"]
        self.assertEqual(
            status_context(0, "## Running agents\nNone\n", ""), "# The loop's status\n\n## Running agents\nNone\n"
        )


class StatusLineTest(HookTest):
    def setUp(self):
        super().setUp()
        accounts = [
            {"name": "claude", "provider": "claude"},
            {"name": "claude-2", "provider": "claude", "home": "~/c2"},
        ]
        usage.ACCOUNTS.write_text(json.dumps(accounts))
        usage.ORCHESTRATOR.write_text(json.dumps({"account": "claude"}))

    def show(self) -> str:
        status = {
            "transcript_path": "session.jsonl",
            "context_window": {"context_window_size": 200_000},
            "rate_limits": {"five_hour": {"used_percentage": 42.0, "resets_at": REQUEST_TIME + 3600}},
        }
        return self.run_script("hooks/status-line", stdin=json.dumps(status))[1]

    def test_shows_how_much_of_each_limit_is_used(self):
        self.assertEqual(self.show(), "five hour 42% used\n")

    def test_records_the_orchestrators_transcript_and_window(self):
        self.show()
        self.assertEqual(usage.orchestrator(), {"account": "claude", "transcript": "session.jsonl", "window": 200_000})

    def test_another_accounts_session_leaves_the_orchestrators_record_alone(self):
        os.environ["CLAUDE_CONFIG_DIR"] = "~/c2"
        self.show()
        self.assertEqual(usage.orchestrator(), {"account": "claude"})

    def test_a_second_session_on_the_orchestrators_account_leaves_its_record_alone(self):
        pid = self.orchestrate_elsewhere()
        self.show()
        self.assertNotIn("transcript", usage.orchestrator())
        self.assertEqual(usage.orchestrator()["pid"], pid)


class WatcherHookTest(HookTest):
    def stop(self, event: dict) -> str:
        return self.run_script("hooks/watcher", stdin=json.dumps(event))[1]

    def test_asks_the_orchestrator_to_start_the_watcher(self):
        output = json.loads(self.stop({"hook_event_name": "Stop"}))
        self.assertEqual(output["decision"], "block")
        self.assertIn("scripts/watch isn't running", output["reason"])

    def test_lets_the_turn_end_when_a_stop_hook_has_already_blocked_it(self):
        self.assertEqual(self.stop({"hook_event_name": "Stop", "stop_hook_active": True}), "")

    def test_a_launched_agent_isnt_asked(self):
        self.assertEqual(self.stop({"hook_event_name": "Stop", "agent_type": "builder"}), "")

    def test_a_second_session_in_the_checkout_isnt_asked(self):
        self.orchestrate_elsewhere()
        self.assertEqual(self.stop({"hook_event_name": "Stop"}), "")

    def test_a_running_watcher_needs_no_other(self):
        (self.loop / "watch.pid").write_text(str(self.start_process("watch")))
        self.assertEqual(self.stop({"hook_event_name": "Stop"}), "")

    def test_another_program_with_the_watchers_old_process_id_isnt_the_watcher(self):
        (self.loop / "watch.pid").write_text(str(self.start_process("vim")))
        self.assertIn("scripts/watch isn't running", self.stop({"hook_event_name": "Stop"}))


class SessionStartLimitTest(HookTest):
    def test_a_context_over_the_hooks_limit_is_cut_to_it(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.load_script("hooks/session-start")["respond"]("a" * 20_000)
        context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), 10_000)
        self.assertTrue(context.startswith("a" * 9_000))
