---
name: building
description: How a builder agent turns one issue into a pull request. Builders preload it.
---

# Building

The orchestrator gives you the number of one issue, with its branch `issue-<number>` checked out.

1. Read the issue with `gh issue view <number> --comments`, and the answers of any research issue that blocks it.
2. Make the change as "Make a change" in AGENTS.md describes, until it meets "Definition of done".
3. End with a short report: the pull request's link, any problem you noticed outside the issue, and anything you couldn't do.

## Hand off to a successor

When a notice tells you to hand off or wind down:

1. Commit your work.
2. Save a note for the builder after you as `.loop/handoff.md`. The note says what's done, what's left in order, the files and line ranges you were working in, the commands you ran, and any open question.
3. End with your report.
