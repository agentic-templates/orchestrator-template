import unittest

import launching
import meter
import usage

ACCOUNTS = [usage.Account("claude", "claude", None), usage.Account("codex", "codex", None)]


def decisions(claude_room: int, codex_room: int) -> dict[str, meter.Decision]:
    def decision(room: int) -> meter.Decision:
        return meter.Decision("start" if room else "hold", room, 0, 40.0, 0.1)

    return {"claude": decision(claude_room), "codex": decision(codex_room)}


class ChooseAccountTest(unittest.TestCase):
    def test_a_requested_account_with_room_is_used(self):
        account, _ = launching.choose_account(decisions(1, 3), ACCOUNTS, "claude", force=False)
        self.assertEqual(account.name, "claude")

    def test_a_requested_account_without_room_is_refused_with_the_meter_report(self):
        account, reason = launching.choose_account(decisions(0, 3), ACCOUNTS, "claude", force=False)
        self.assertIsNone(account)
        self.assertIn("without --account", reason)
        self.assertIn("Start the next agent on: codex", reason)

    def test_force_starts_on_a_requested_account_without_room(self):
        account, _ = launching.choose_account(decisions(0, 0), ACCOUNTS, "claude", force=True)
        self.assertEqual(account.name, "claude")

    def test_without_a_request_the_account_with_room_is_used(self):
        account, _ = launching.choose_account(decisions(0, 2), ACCOUNTS, None, force=False)
        self.assertEqual(account.name, "codex")

    def test_without_room_anywhere_nothing_starts(self):
        account, reason = launching.choose_account(decisions(0, 0), ACCOUNTS, None, force=False)
        self.assertIsNone(account)
        self.assertIn("No account has room", reason)

    def test_an_unknown_account_is_refused(self):
        account, reason = launching.choose_account(decisions(1, 1), ACCOUNTS, "claude-9", force=False)
        self.assertIsNone(account)
        self.assertIn("No account is called claude-9", reason)


if __name__ == "__main__":
    unittest.main()
