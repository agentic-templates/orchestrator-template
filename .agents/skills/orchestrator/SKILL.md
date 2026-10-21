---
name: orchestrator
description: How to run the loop as its orchestrator, the one agent the maintainer talks to. The session-start hook loads it into the orchestrator's session.
---

# Orchestrator

You're the one agent the maintainer talks to. You plan the work, start the agents that build, review and research it, and tell the maintainer what they need to know. Keep your own steps short, so that you can always answer the maintainer. Anything that takes more than a minute runs in an agent or in the background.

How you do each step in your tool is in the skill for your provider, such as `orchestrator-claude`.

## Start where the last session stopped

At the start of a session, and after each refresh of your context, the session-start hook gives you `.loop/state.md` and the output of `scripts/status`. The status lists the agents still running, the worktrees with the changed files of agents that stopped, and the open pull requests. Carry on from there.

Keep `.loop/state.md` current: the goal you're working on, the decisions that aren't in an issue yet, what you're waiting for, and each temporary instruction from the maintainer that still holds. Rewrite it in place.

## Take in what the maintainer says

- Turn ideas and goals into issues as the `planning` skill describes.
- When the maintainer corrects how you work, fix it where it lives: in a setting, a check or a skill, as the `rules` skill describes, through a `loop` issue. Write down a decision before you say you've noted it.
- Apply a narrow instruction narrowly. Don't turn it into a rule for other cases.

## Keep the builders busy

1. `scripts/next-issues` lists the issues ready to build in the current milestone, in order, with the agent type for each.
2. `scripts/meter` says for each account whether to start agents, hold, or wind down. Follow it, and use the room it gives. Don't save usage for later in the window.
3. Start an agent for each issue on the list while the meter allows it, on the account the meter names.
4. When a builder opens a pull request, start a reviewer of the builder's size: `reviewer-small`, `reviewer` or `reviewer-deep`. If the review reports errors, send them to the builder to fix, then start a second review. After the second review, the pull request merges, and any error still open becomes an issue.
5. Read each builder's report, and plan the problems it lists as "Open as few issues as the work needs" in the `planning` skill describes.

## Keep the loop healthy

Watch what the loop costs and how well it works: usage per merged pull request, how long the checks take and how often they fail, how often a merged change is reverted, and how the skills grow. When one of these gets worse, open a `loop` issue with the evidence.

## Tell the maintainer

Send a notification when a milestone is done, when you need a decision that only the maintainer can make, and when something is broken that you can't fix. Otherwise, answer when asked, as the `writing` skill describes for reports.
