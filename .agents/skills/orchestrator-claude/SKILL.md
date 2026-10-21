---
name: orchestrator-claude
description: How the orchestrator does each step in Claude Code. The session-start hook loads it into a Claude Code orchestrator's session.
---

# Orchestrator in Claude Code

- **Start an agent:** run `scripts/launch <agent type> <number>` in the background, as its `--help` describes, whatever the account. Its exit wakes you with the agent's report.
- **Send a running agent a message:** `scripts/message <agent ID> <message>`. For an agent that has stopped, such as a builder whose pull request got review errors, run `scripts/launch --resume <agent ID> --message "<message>"` in the background.
- **Watch the loop:** `scripts/watch`, in the background. Its exit tells you what changed.
- **Wait for anything else:** a background command or Monitor that exits when the event happens. Never wait in the foreground.
- **Tell the maintainer:** PushNotification.
