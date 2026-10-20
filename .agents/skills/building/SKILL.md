---
name: building
description: How a builder agent turns one issue into a pull request. Builders preload it.
---

# Building

The orchestrator gives you the number of one issue.

1. Read the issue with `gh issue view <number> --comments`, and the answers of any research issue that blocks it.
2. Run `git switch -c issue-<number> origin/main`.
3. Make the change as "Make a change" in AGENTS.md describes, until it meets "Definition of done".
4. If a notice tells you to wind down, finish the step you're on, commit, push the branch, and say in your report what's left.
5. End with a short report: the pull request's link, any problem you noticed outside the issue, and anything you couldn't do.
