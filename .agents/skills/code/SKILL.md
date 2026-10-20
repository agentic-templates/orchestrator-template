---
name: code
description: Rules for design, functions, comments, errors and tests. Read it before you write, change or review code or tests.
---

# Code

Write code that a reader understands the first time they read it. If a rule here would make a piece of code harder to read, choose the clearer code and say why in the pull request.

## Keep the design simple

- Build the simplest design that meets what the project needs now, including any accuracy or speed that the issue asks for. Add a layer, an option or a general version only when the project needs it, such as a storage interface once there's a second storage backend.
- Follow the conventions the code already uses for naming, file layout and error handling. If a convention makes code harder to read, say so in your report.
- Keep each decision the code makes, such as a limit or how a total is rounded, in one place, so that changing it takes one edit. Before you write code that makes a decision, check whether other code already makes it. Keep separate decisions apart even when they look alike. If the limits on a description and on a category name are both 50 characters, use a constant for each.
- Keep the code that calculates a result apart from the code that reads input or writes output, such as files, the network, the clock and environment variables. The function that totals this month's expenses takes the expenses and today's date as arguments and returns the total. Its caller reads the file and the clock.
- Fix the cause of a problem, not only the place where it shows up. If listing expenses crashes on an expense with no category, make the code that reads expenses give it the category "none". Don't catch the crash where the list is printed.
- If the code you need to change is hard to change, refactor it first. Refactor only the code your change touches. A refactoring passes every test without changing what any test checks. If no test runs the code, write one first.
- Remove dead code in the code your change touches.

## Write functions a reader can follow

- Give each function one purpose, and name it for that purpose.
- Return early instead of nesting conditions.
- Prefer a plain loop and named steps to a clever one-liner.
- Choose names that explain themselves, in whole words. Name a boolean as a yes-or-no question, such as `is_empty`.

## Add complexity only for a requirement or a measurement

- Add complex code only when a simpler version would fail a requirement, or when a measurement shows what the complex version gains. Name the requirement or the measurement in a comment.
- Keep the complex code in one place behind a plain interface.
- For an optimization, keep a plain version in the tests and check that both give the same results.

## Comment only what the code can't show

A comment gives what a later editor needs to know before they change the line: a reason, a hidden constraint or a surprise. It doesn't say what the code does or how it changed. Make the code clearer before you add one.

- Not `# Wait one second`, above `time.sleep(1)`
- Not `# Now waits between requests instead of retrying`, above `time.sleep(1)`
- But `# The bank's API rejects more than one request a second.`, above `time.sleep(1)`

## Fail with a clear error

- Check input where it enters the program: arguments, environment variables, files, network traffic, and the arguments to a library's public functions. Trust values from inside the program.
- Catch an error only where you can recover from it or add useful context. Never hide an error, such as by returning an empty list when a file can't be read.

## Test behavior

- Test what the code does through its public interface, not how it does it.
- Check each result against what the requirement says. Don't take the expected value from the code's own output, and don't compute it the way the code does, because then the test repeats the code instead of checking it.
- Test the edges of each requirement as well as a typical case, such as empty input and the values on both sides of a limit.
- Write the tests for the behavior your issue adds or fixes before you change the code, and see them fail.
- Keep tests independent of their order and of the network, the clock and random numbers. Use a stand-in only for code that reads the clock or the network, never for the project's own code.
- Never weaken a test to make it pass, such as by loosening what it expects or skipping it. If you think a test is wrong, leave it and say so in your report.
- Delete a test only when your issue removes its requirement, or when it checks only what other tests check already.
- Write code that works for every input, not only the inputs in the tests.
- Don't loosen a check to make it pass. Add a suppression comment such as `# noqa` only when you can't fix the code without making it worse, and give the reason in it.

## Keep the checks fast

- Run a test only when a change touches what it tests.
- Move slow tests of the product as a whole, such as end-to-end tests, into a command that CI runs after each merge.
