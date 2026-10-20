---
name: writing
description: Rules for anything that others read, such as docs, issues, pull requests, commit messages, comments, error messages and reports. Read it before you write any of these.
---

# Writing

The aim is text that a reader understands the first time they read it.

## Know your reader

- Write for a reader who knows the project's field, reads once and hasn't seen your conversation.
- Leave out what the reader already knows and what the code already shows.
- Leave out how something works inside, unless the reader must act on it.
- Start with what the reader came for: what they can do, what changed or what went wrong.

## Use plain words

- Use everyday words for the actual action, not vague verbs such as "handles", "deals with" or "manages". Write "adds the expense to the month's total", not "processes the expense".
- Keep a technical term only when it's the precise name for the thing. Explain it where it first appears, unless people in the reader's field use it every day.
- Use one term for one thing, everywhere.
- Don't use metaphors or marketing words such as "seamless", "powerful" or "blazing fast".
- Cut filler such as "simply", "just", "basically" and "note that".
- Replace a vague judgment with the fact behind it. Write "imports 10,000 expenses in 2 seconds", not "fast".

For example, in a README for people who use an expense tracker:

- Not "The import pass hashes each row and drops collisions."
- But "When you import a file, the app skips the expenses it already has."
- Not "The app handles foreign currencies differently."
- But "The app leaves expenses in a foreign currency out of the total and lists them below it."

## Write clear sentences

- Put one idea in each sentence, and say each thing once. When two sentences say the same thing, merge them.
- Keep the words that link ideas, such as "because", "so", "but" and "unless". Give a reason only when the reader needs it to act correctly.
- Write two sentences instead of joining them with a semicolon or a dash.
- Say who does what. Write "The app deletes the expense", not "The expense is deleted".
- In a step the reader follows, put the condition first: "If the disk is full, free at least 2 GB and run it again."
- Give exact numbers with their units, and exact names, commands and paths.
- Put results and comparisons in a table.

## Claim only what is true

- State something as a fact only when you saw it in a command's output, a file or a web page, such as that a check passed.
- Mention planned work only when an open issue describes it, and link to that issue.

## Write error messages

An error message says what failed and what the reader can do about it.

- Not "Invalid file."
- But "bank.csv has 60,000 rows, and the limit is 50,000. Split it into two files and try again."

## Write issues

- Write an issue in a few sentences that state its result, such as what to build or the question to answer. Link to details elsewhere instead of repeating them.
- "What should change" says what a user or developer will notice once the issue is built, not how to build it.
- "Why" gives the problem and its evidence.
- "Done when" lists results that someone can check, and doesn't repeat "What should change".
- When a decision or a new detail changes the issue, rewrite the sentences it changes, so that the body reads as if it had been written that way from the start.

## Write pull requests

- Write for a developer who joins the project later: what changed, why, and what that reader might otherwise get wrong. The diff shows how the code works, so don't describe it.
- Describe the final change, not the history of the branch. After a round of fixes, rewrite the description instead of adding to it.
- Don't list the changed files, and don't repeat what CI shows. Say how you checked the change only where CI doesn't show it, such as a test by hand.
- Name each existing test you changed or deleted, and why.

## Write reports

- Start with what the reader must decide, or with what changed.
- Show the result, such as the new wording, instead of describing it.
- Give each decision with the option you recommend.
- Leave out the steps you took, unless the reader asks for them.
