import unittest

import prose


def messages(text: str) -> list[str]:
    return [problem.message for problem in prose.check(text)]


class CheckTest(unittest.TestCase):
    def test_accepts_plain_sentences(self):
        self.assertEqual(messages("The app adds the expense to the month's total.\n"), [])

    def test_finds_a_semicolon_and_a_joining_dash(self):
        self.assertEqual(
            messages("It adds the expense; then it saves.\nIt saves — then it exits.\n"),
            [
                (
                    "Write two sentences instead of joining them with a semicolon. In a title or a heading, which "
                    'holds one sentence, link the two parts with a word such as "and", or keep only one of them.'
                ),
                "Write two sentences, or use a comma, instead of joining with a dash.",
            ],
        )

    def test_names_the_line_of_a_filler_word(self):
        self.assertEqual(prose.check("First line.\nIt simply works.\n"), [prose.Problem(2, 'Cut "simply".')])

    def test_leaves_out_code_quotes_front_matter_and_ranges(self):
        text = '---\nname: x; y\n---\nWrite "it simply works" as `a; b`.\nIt takes 1–3 minutes.\n```\nx; y\n```\n'
        self.assertEqual(messages(text), [])

    def test_lists_problems_in_line_order(self):
        lines = [problem.line for problem in prose.check("It simply works.\nSee [here](https://example.com).\n")]
        self.assertEqual(lines, [1, 2])

    def test_ends_front_matter_only_at_a_line_of_three_dashes(self):
        self.assertEqual(messages("---\nname: x\n----\nsimply; here\n---\nIt works.\n"), [])

    def test_leaves_out_an_html_comment_and_keeps_the_line_numbers(self):
        text = "<!-- note\nsimply\nsimply -->\nIt simply works.\n"
        self.assertEqual(prose.check(text), [prose.Problem(4, 'Cut "simply".')])

    def test_finds_vague_link_text(self):
        self.assertEqual(messages("See [here](https://example.com).\n"), ["Make the link text name the page it opens."])


class ProseWordsTest(unittest.TestCase):
    def test_counts_sentences_and_list_items_but_not_tables_headings_or_code(self):
        text = "# A heading\n\nOne two three.\n\n- four five\n\n| six | seven |\n\n```\neight nine\n```\n"
        self.assertEqual(prose.prose_words(text), 5)

    def test_counts_the_words_of_a_line_that_starts_with_an_issue_number(self):
        self.assertEqual(prose.prose_words("#91 asked for it.\n"), 4)


if __name__ == "__main__":
    unittest.main()
