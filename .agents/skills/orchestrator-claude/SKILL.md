---
name: orchestrator-claude
description: How the orchestrator does each step in Claude Code. The session-start hook loads it into a Claude Code orchestrator's session.
---

# Orchestrator in Claude Code

- **Start a builder on this account:** the Agent tool, with the agent type that `scripts/next-issues` names, `run_in_background: true`, and a prompt that names the issue. The agent type gives it a worktree of its own.
- **Start a builder on another account or on Codex:** run `scripts/launch` in the background, as its `--help` describes. Its exit wakes you.
- **Send a builder a message:** SendMessage for an agent on this account, and `scripts/message` for one that `scripts/launch` started.
- **Wait for CI, a merge or a reset:** a background command or Monitor that exits when the event happens. Never wait in the foreground.
- **Tell the maintainer:** PushNotification.
