---
name: reviewing
description: How to review a pull request or a plan. Read it before you review one.
---

# Reviewing

## Review a pull request

- Compare the change with its issue's "Done when" list and with "Definition of done" in AGENTS.md.
- Report only what must change: wrong behavior, a missing test, a security problem, or code and text that break the skills. Give each error a case that shows it, such as an input and the wrong result, and say what to change.
- A mistake an agent could make by accident is an error. A command written on purpose to get past a hook or a check is not.
- Don't ask for changes that a formatter would make, or for work outside the issue. Put a problem outside the issue in your report instead.
- Run `scripts/check --review --base <base commit>`, and judge each function it lists in context. Ask for a change only where the function is hard to follow. Moving code into helpers only to lower a count isn't a fix, and neither is a split that makes the code harder to read.
- Read the pull request's description against the `writing` skill. You may cut or reword sentences, but don't ask for more text.
- In a second review, read only what changed since the first one.

Post the review with `gh pr review <number> --comment --body-file <file>`, with the file written outside the repository.

## Review a plan

Check that each issue meets "Definition of ready" in the `planning` skill, that the blocked-by links match the order the changes need, and that no two issues that can run at once change the same code. Report only what must change, as for a pull request.

## Review the code for security problems

Start where the program decides who may do what, where it uses secrets or runs other programs, and where input from outside enters it. Include the workflows in `.github/`. Describe each problem by what's wrong, the code it's in and what the fix needs, without the steps of an attack. Report each one with how serious it is and the fix you recommend.
