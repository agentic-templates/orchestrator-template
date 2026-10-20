import unittest

from backlog import Issue, agent_type, ready


def issue(number: int, *labels: str, by_maintainer: bool = True, blockers: tuple[int, ...] = ()) -> Issue:
    return Issue(number, f"Issue {number}", frozenset(labels), by_maintainer, blockers)


class ReadyTest(unittest.TestCase):
    def numbers(self, issues: list[Issue], in_progress: set[int] = frozenset()) -> list[int]:
        return [item.number for item in ready(issues, set(in_progress))]

    def test_lists_the_oldest_issues_first(self):
        self.assertEqual(self.numbers([issue(3), issue(1), issue(2)]), [1, 2, 3])

    def test_leaves_out_issues_by_others_waiting_or_started(self):
        issues = [
            issue(1, by_maintainer=False),
            issue(2, "needs-decision"),
            issue(3, "needs-triage"),
            issue(4, blockers=(9,)),
            issue(5),
            issue(6),
        ]
        self.assertEqual(self.numbers(issues, in_progress={5}), [6])

    def test_starts_one_loop_issue_at_a_time(self):
        self.assertEqual(self.numbers([issue(1, "loop"), issue(2, "loop"), issue(3)]), [1, 3])
        self.assertEqual(self.numbers([issue(1, "loop"), issue(2, "loop")], in_progress={1}), [])


class AgentTypeTest(unittest.TestCase):
    def test_picks_the_agent_from_the_size_or_research_label(self):
        self.assertEqual(agent_type(issue(1, "small", "bug")), "builder-small")
        self.assertEqual(agent_type(issue(1, "deep")), "builder-deep")
        self.assertEqual(agent_type(issue(1, "research")), "researcher")
        self.assertEqual(agent_type(issue(1, "feature")), "builder")


if __name__ == "__main__":
    unittest.main()
