import unittest

import files


def messages(path: str, text: str) -> list[str]:
    return [problem.message for problem in files.check_text(path, text)]


class CheckTextTest(unittest.TestCase):
    def test_finds_a_home_path_and_a_private_email(self):
        home = "/Us" + "ers/alex/notes.md"
        self.assertEqual(len(messages("a.md", f"See {home} or mail alex" + "@mail.test.\n")), 2)

    def test_accepts_public_addresses(self):
        self.assertEqual(messages("a.md", "Mail 1+alex@users.noreply.github.com or bot@example.com.\n"), [])

    def test_finds_unfinished_work(self):
        self.assertEqual(len(messages("a.py", "x = 1  # TO" + "DO: round the total\n")), 1)

    def test_needs_a_reason_for_each_suppression(self):
        noqa = "# no" + "qa: complexipy"
        self.assertEqual(len(messages("a.py", f"def f():  {noqa}\n")), 1)
        self.assertEqual(messages("a.py", f"def f():  {noqa}, because each branch reads one field\n"), [])
        self.assertEqual(messages("a.rs", "// Allowed because the parser needs every case.\n#[allow(clippy::x)]\n"), [])
        self.assertEqual(len(messages("a.ts", "// eslint-" + "disable-next-line no-console\n")), 1)

    def test_limits_the_size_of_skills_only(self):
        text = "word " * 2_500
        self.assertEqual(len(messages(".agents/skills/code/SKILL.md", text)), 1)
        self.assertEqual(messages("docs/notes.md", text), [])


class CheckNameTest(unittest.TestCase):
    def test_rejects_claude_md(self):
        self.assertEqual(len(files.check_name("sub/CLAUDE.md")), 1)
        self.assertEqual(files.check_name("AGENTS.md"), [])


if __name__ == "__main__":
    unittest.main()
