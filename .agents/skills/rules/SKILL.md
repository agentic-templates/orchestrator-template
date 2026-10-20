---
name: rules
description: How to change the rules agents follow, in AGENTS.md, a skill, an agent type, a hook, a setting or a check. Read it before you change any of them.
---

# Rules

- Before you add a rule, check whether a setting, a check or a hook could prevent the mistake instead. Prefer them in that order, and write a rule only when none of them can.
- Put a rule in the skill that matches its subject, and a rule for one folder in an AGENTS.md in that folder.
- Leave out what agents already do without being told, what the files show, and how a mechanism works. A script explains itself in its `--help`.
- Don't repeat a rule that a check enforces, when the check's message says what to do.
- Write a rule for the common case, without exceptions. If a rare case doesn't fit, change the rule so that it covers the case, or leave the case to the agent's judgment.
- Rewrite a rule in place. Don't add a sentence that corrects the one before it.
- Before you remove a rule, find out why it was added, with `git log -S "<words>"`. Keep it if it prevents a mistake that happened.
- A change to the rules merges only if the loop's test of recent issues does as well or better at no higher cost.
