import unittest

import pull_request

BODY = "Closes #12\n\nThe import skips expenses it already has, so importing a file twice no longer doubles them.\n"


class TitleTest(unittest.TestCase):
    def test_accepts_a_type_scope_and_breaking_mark(self):
        for title in ("feat: skip expenses that were already imported", "fix(import)!: keep the original dates"):
            self.assertEqual(pull_request.check_title(title), [], title)

    def test_rejects_a_title_without_a_known_type(self):
        self.assertEqual(len(pull_request.check_title("Skip duplicate expenses")), 1)
        self.assertEqual(len(pull_request.check_title("feature: skip duplicate expenses")), 1)


class BodyTest(unittest.TestCase):
    def test_accepts_a_short_description(self):
        self.assertEqual(pull_request.check_body(BODY, is_research=False), [])

    def test_needs_exactly_one_closed_issue(self):
        self.assertEqual(len(pull_request.check_body("No issue.\n", is_research=False)), 1)
        self.assertEqual(len(pull_request.check_body("Closes #1\nCloses #2\n", is_research=False)), 1)

    def test_rejects_a_long_description_unless_it_closes_a_research_issue(self):
        long_body = "Closes #12\n\n" + "The import reads each row once. " * 100
        self.assertEqual(len(pull_request.check_body(long_body, is_research=False)), 1)
        self.assertEqual(pull_request.check_body(long_body, is_research=True), [])

    def test_does_not_count_tables_toward_the_length(self):
        table = "| model | result |\n|---|---|\n" + "| haiku low | passed 28 of 30 issues |\n" * 80
        self.assertEqual(pull_request.check_body(BODY + "\n" + table, is_research=False), [])

    def test_rejects_a_list_of_changed_files(self):
        files = "- `scripts/check`\n- `scripts/lib/prose.py`\n- `AGENTS.md`\n"
        self.assertEqual(len(pull_request.check_body(BODY + "\n" + files, is_research=False)), 1)

    def test_rejects_the_history_of_the_branch(self):
        for sentence in ("At 12:30 the check failed.", "The second review round found a gap."):
            self.assertEqual(len(pull_request.check_body(BODY + sentence + "\n", is_research=False)), 1, sentence)

    def test_rejects_a_paragraph_in_a_table_cell(self):
        cell = " ".join(["word"] * 50)
        self.assertEqual(len(pull_request.check_body(f"{BODY}\n| a | {cell} |\n", is_research=False)), 1)

    def test_rejects_headings_and_bold_text_that_stands_as_one(self):
        body = BODY + "\n## Why\n\n**What changed:** the import skips them.\n\n__Tests__\n\n**Why**: the import doubled them.\n"
        after_the_line_number = (
            "is a heading, or starts with bold text that stands as one. Write the description without them, "
            "in sentences and lists."
        )
        self.assertEqual(
            pull_request.check_body(body, is_research=False),
            [f"Line {number} {after_the_line_number}" for number in (5, 7, 9, 11)],
        )

    def test_accepts_sentences_with_bold_words_or_an_issue_number_first(self):
        sentences = "The import **skips** expenses it has.\n- **Dates** stay as they were.\n**Both** imports agree.\n#12 asked for it.\n"
        self.assertEqual(pull_request.check_body(BODY + sentences, is_research=False), [])


if __name__ == "__main__":
    unittest.main()
