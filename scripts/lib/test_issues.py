import unittest

import issues

BODY = "## What should change\n\nThe import skips duplicates.\n\n## Why\n\nImports double the totals.\n\n## Done when\n\n- A second import adds nothing.\n"


class CheckBodyTest(unittest.TestCase):
    def test_accepts_the_three_sections(self):
        self.assertEqual(issues.check_body(BODY), [])

    def test_names_each_missing_section(self):
        self.assertEqual(
            issues.check_body("## Why\n\nImports double the totals.\n"),
            ['Add the section "## What should change".', 'Add the section "## Done when".'],
        )

    def test_rejects_the_story_of_a_session(self):
        self.assertEqual(len(issues.check_body(BODY + "\nAt 12:30 the import failed.\n")), 1)


if __name__ == "__main__":
    unittest.main()
