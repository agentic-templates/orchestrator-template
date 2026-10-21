import unittest

from terminal import read

# Lines as Claude Code 2.1.304 drew them in tmux, shortened.
RULE = "\x1b[38;5;244m" + "─" * 40 + " builder-small ─"
BOTTOM = "\x1b[38;5;244m" + "─" * 56
STATUS = "\x1b[39m  \x1b[38;5;246mfive hour 2% · seven day 50%\x1b[39m"
PLACEHOLDER = '\x1b[39m❯\xa0\x1b[2mTry "fix lint errors"\x1b[0m'
SPINNER = "\x1b[38;5;174m✶\x1b[39m \x1b[38;5;216mTransfiguring…\x1b[38;5;174m \x1b[38;5;246m(17s · ↓\x1b[39m 88 tokens)"
MENU = (
    "  \x1b[38;5;153m❯ /\x1b[1mclear\x1b[0m\x1b[38;5;153m      Start a new session with empty context\x1b[39m",
    "                  \x1b[38;5;153m(resumable with /resume)\x1b[39m",
)


def screen(*lines: str) -> str:
    return "\n".join(["❯ An earlier message", "⏺ An earlier answer.", "", *lines]) + "\n"


class ReadTest(unittest.TestCase):
    def test_an_idle_box_with_its_placeholder_is_empty(self):
        result = read(screen("✻ Worked for 23s · done 9:01 PM", "", RULE, PLACEHOLDER, BOTTOM, STATUS))
        self.assertEqual(result.box, "")
        self.assertFalse(result.is_busy)

    def test_reads_typed_text(self):
        result = read(screen(RULE, "\x1b[39m❯\xa0hello there", BOTTOM, STATUS))
        self.assertEqual(result.box, "hello there")

    def test_reads_text_on_several_lines(self):
        result = read(screen(RULE, "\x1b[39m❯\xa0line one", "  line two", BOTTOM, STATUS))
        self.assertEqual(result.box, "line one\nline two")

    def test_the_command_menu_above_the_box_is_not_the_box(self):
        result = read(screen(*MENU, RULE, "\x1b[39m❯\xa0\x1b[38;5;153m/clear\x1b[39m", BOTTOM, STATUS))
        self.assertEqual(result.box, "/clear")
        self.assertFalse(result.is_busy)

    def test_a_running_turn_is_busy_with_text_typed_meanwhile(self):
        result = read(screen(SPINNER, "", RULE, "\x1b[38;5;246m❯\xa0\x1b[39mqueued words", BOTTOM, STATUS))
        self.assertTrue(result.is_busy)
        self.assertEqual(result.box, "queued words")

    def test_a_dialog_shows_no_box(self):
        dialog = ("Do you want to proceed?", "❯ 1. Yes", "  2. No")
        result = read(screen(RULE, *dialog, "", "Esc to cancel"))
        self.assertIsNone(result.box)


if __name__ == "__main__":
    unittest.main()
