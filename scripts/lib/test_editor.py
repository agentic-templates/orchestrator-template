from temporary_loop import TemporaryLoopTest


class EditorTest(TemporaryLoopTest):
    def setUp(self):
        super().setUp()
        self.taking_input = self.loop / "taking-input"
        self.taking_input.write_text("")
        self.draft = self.root / "draft.md"

    def test_moves_the_text_in_the_input_box_to_unsent_and_says_it_took_it(self):
        self.draft.write_text("Can you look at\n")
        self.assertEqual(self.run_script("editor", str(self.draft)), (0, ""))
        self.assertEqual(self.draft.read_text(), "")
        self.assertEqual([path.read_text() for path in (self.loop / "unsent").iterdir()], ["Can you look at\n"])
        self.assertFalse(self.taking_input.exists())

    def test_an_empty_input_box_leaves_nothing_in_unsent(self):
        self.draft.write_text("\n")
        self.assertEqual(self.run_script("editor", str(self.draft)), (0, ""))
        self.assertFalse((self.loop / "unsent").exists())
        self.assertFalse(self.taking_input.exists())
