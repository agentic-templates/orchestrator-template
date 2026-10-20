---
name: planning
description: How to turn the maintainer's goals and ideas into issues, and how to keep the issues few and in the right order. Read it before you plan work or open an issue.
---

# Planning

## Labels

- Type, one per issue, by what changes for users:
  - `bug`: something is broken, such as a crash, a wrong result, or docs that don't match the product.
  - `feature`: a change to what users can do or read, other than a fix.
  - `maintenance`: work that leaves what users can do unchanged, such as speed-ups, refactoring, tooling or tests.
  - `research`: a question to answer before anyone builds.
- Size, which picks the builder: `small` for a change of a few lines, `deep` for work that needs more thought than most, such as code that many callers depend on or a failure nobody understands yet. Most issues get neither.
- `loop`: a change to how the loop itself works, such as its skills, agents, hooks or checks.
- `needs-decision`: waiting for the maintainer to answer a question that the issue states, with the option you recommend.
- `needs-triage`: opened by someone outside the project, and not judged yet.

## Take in ideas

1. Split the maintainer's message into single ideas. One idea is one result that someone wants. Merge ideas that ask for the same result.
2. Judge each idea on its merits, not on how much the maintainer seems to want it. Does it fit "What this project is" in AGENTS.md? Does an issue already cover it? Is it worth what it costs, counting what it adds for agents to read and run in every later session?
3. Give each idea one verdict, and carry it out:
   - **Build:** it becomes an issue, or new details for an issue that already covers it.
   - **Research first:** a research issue for the question, which blocks the issue for the idea.
   - **Drop:** nothing is created.
   - **Ask:** only for what "Decide or ask" in AGENTS.md lists. The issue gets `needs-decision`.
4. Show the maintainer the ideas, verdicts and reasons, one line each.

## Open as few issues as the work needs

- Open issues with `scripts/new-issue`, which shows the open issues that look similar first.
- When an issue that hasn't started yet covers the same code or result, change its body instead of opening another.
- When a builder reports a problem outside its issue, open an issue only if something is broken or the current goal needs it. Otherwise, add it to an existing issue or leave it out.
- Only one `loop` issue is open and ready at a time.

## Plan a goal

A goal is what a user can do afterwards that they can't do now. Each goal is a milestone, and the loop builds only the issues in the current milestone.

1. Choose the issues the goal needs, and add the ones it lacks.
2. Split work that doesn't fit in one pull request into issues that each leave main working. Make the big one a parent with `gh issue create --parent <parent>`.
3. Link each issue to the issues whose change it needs first, with `gh issue edit <issue> --add-blocked-by <other>`. Also link two issues that change the same code, so that the second one builds on the first one's merged result.
4. Make every issue meet "Definition of ready".

## Definition of ready

An issue is ready when all of these are true:

- It asks for one change that fits in one pull request.
- Someone could check each "Done when" item by running or looking at something. Where speed or accuracy matters, the item gives a number.
- Nothing is left that only the maintainer can decide.
- "What should change" says what's out of scope, if a builder might otherwise do more.
- It's blocked by every issue whose change it needs first.
- It has a type label, and a size label if it needs one.

A research issue is ready when its question is clear. Its "Done when" asks for a comment with the answer, a recommendation and the evidence.

## Triage issues from others

Judge each `needs-triage` issue as step 2 of "Take in ideas" describes. Close a duplicate with `gh issue close <issue> --duplicate-of <other>`. Answer a question about using the project in a comment, and close the issue. Close an issue you drop with a comment that says why. For an issue you accept, write a new issue in your own words, and close the original as a duplicate of it.
