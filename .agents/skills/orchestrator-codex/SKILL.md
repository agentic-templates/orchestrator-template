---
name: orchestrator-codex
description: How the orchestrator does each step in Codex. The session-start hook loads it into a Codex orchestrator's session.
---

# Orchestrator in Codex

- **Start a builder on this account:** `spawn_agent`, with a prompt that names the issue and the agent type that `scripts/next-issues` names, and tells it to read the matching file in `.claude/agents/` and the skills it lists.
- **Start a builder on another account or on Claude Code:** run `scripts/launch` in the background, as its `--help` describes.
- **Send a builder a message:** `send_message` for an agent you spawned, and `scripts/message` for one that `scripts/launch` started.
- **Wait for an agent:** `wait_agent`. Wait for CI, a merge or a reset with a background command that exits when the event happens.
