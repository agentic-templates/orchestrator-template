import re
import unittest
from pathlib import Path

import files

# The plain version of files.EMAIL_OR_RUN, which must find the same addresses as it. Change the two together.
PLAIN_EMAIL = re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")


def messages(path: str, text: str) -> list[str]:
    return [problem.message for problem in files.check_text(path, text)]


def plain_private_addresses(line: str) -> list[str]:
    addresses = [match.group() for match in PLAIN_EMAIL.finditer(line)]
    return [address for address in addresses if not files.PUBLIC_EMAIL.search(address)]


class CheckTextTest(unittest.TestCase):
    def test_finds_a_home_path_and_a_private_email(self):
        home = "/Us" + "ers/alex/notes.md"
        self.assertEqual(len(messages("a.md", f"See {home} or mail alex" + "@mail.test.\n")), 2)
        self.assertEqual(len(messages("a.md", "See /ho" + "me/alex/notes.md.\n")), 1)
        self.assertEqual(len(messages("a.md", "Mail xgit" + "@github.com or bot" + "@example.com.test.\n")), 2)

    def test_accepts_public_addresses(self):
        self.assertEqual(messages("a.md", "Mail 1+alex@users.noreply.github.com or bot@example.com.\n"), [])
        self.assertEqual(messages("a.md", "Run `git clone git@github.com:owner/repo.git`.\n"), [])

    def test_finds_the_same_private_addresses_as_the_plain_email_pattern(self):
        # Each private address is split before its @, so that the check passes on this file.
        lines = [
            "",
            "alex" + "@mail.test",
            "Mail a.b+c-d_e" + "@sub.mail-x.test, or (élodie" + "@MAIL.TEST).",
            "alex@mail.t, alex@localhost, alex@mail.test2, alex@mail.test_x, alex@.test, @mail.test",
            "a" * 50 + ", " + "a." * 50 + ", " + "a" * 50 + "@" + "b" * 50,
            "a@b" + "@mail.test",
            "bot@example.com-alex" + "@mail.test",
            "alex" + "@mail.test+bob" + "@mail.test",
        ]
        for line in lines:
            with self.subTest(line=line):
                expected = [f"Remove the email address {address}." for address in plain_private_addresses(line)]
                self.assertEqual(messages("a.md", line), expected)

    def test_finds_unfinished_work(self):
        self.assertEqual(len(messages("a.py", "x = 1  # TO" + "DO: round the total\n")), 1)

    def test_needs_a_reason_for_each_suppression(self):
        noqa = "# no" + "qa: complexipy"
        self.assertEqual(len(messages("a.py", f"def f():  {noqa}\n")), 1)
        self.assertEqual(messages("a.py", f"def f():  {noqa}, because each branch reads one field\n"), [])
        self.assertEqual(messages("a.rs", "// Allowed because the parser needs every case.\n#[allow(clippy::x)]\n"), [])
        self.assertEqual(len(messages("a.ts", "// eslint-" + "disable-next-line no-console\n")), 1)
        self.assertEqual(len(messages("a.py", "x = f()  # t" + "y: ignore[invalid-assignment]\n")), 1)
        for path, suppression in (
            ("a.py", "import os  # NO" + "QA: F401"),
            ("a.py", "# ruff: no" + "qa: F401"),
            ("a.py", "# flake8: no" + "qa: F401"),
            ("a.py", "# ruff: dis" + "able[F401]"),
            ("a.yml", "- uses: actions/checkout@v4 # zizmor:" + " ignore[unpinned-uses]"),
            ("a.yml", "          # shell" + "check disable=SC2086"),
        ):
            with self.subTest(suppression=suppression):
                self.assertEqual(len(messages(path, suppression + "\n")), 1)

    def test_accepts_code_that_only_looks_like_a_suppression(self):
        for path, line in (("a.sh", "# shellcheck source=lib/common.sh"),):
            with self.subTest(line=line):
                self.assertEqual(messages(path, line + "\n"), [])

    def test_only_docs_and_files_py_may_name_suppressions(self):
        files_py = Path(files.__file__).read_text()
        self.assertNotEqual(messages("scripts/lib/other.py", files_py), [])
        self.assertEqual(messages("docs/linting.md", "Write `# no" + "qa: F401` on the line.\n"), [])

    def test_a_skill_may_have_at_most_10000_characters(self):
        self.assertEqual(messages(".agents/skills/code/SKILL.md", "a\n" * 5_000), [])
        self.assertEqual(len(messages(".agents/skills/code/SKILL.md", "a\n" * 5_000 + "a")), 1)
        self.assertEqual(messages("docs/notes.md", "a\n" * 5_000 + "a"), [])

    def test_agents_md_may_have_at_most_32_kib(self):
        self.assertEqual(messages("AGENTS.md", "a\n" * 16_384), [])
        self.assertEqual(len(messages("AGENTS.md", "a\n" * 16_384 + "a")), 1)
        self.assertEqual(messages("README.md", "a\n" * 20_000), [])


class CheckNameTest(unittest.TestCase):
    def test_rejects_claude_md(self):
        self.assertEqual(len(files.check_name("sub/CLAUDE.md")), 1)
        self.assertEqual(len(files.check_name("CLAUDE.local.md")), 1)
        self.assertEqual(files.check_name("AGENTS.md"), [])


if __name__ == "__main__":
    unittest.main()
