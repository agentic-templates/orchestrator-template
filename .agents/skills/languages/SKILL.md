---
name: languages
description: How to add a language, a tool or a dependency to the project, with the strict settings each language's tools need. Read it before you add one.
---

# Languages and dependencies

## Add a language

1. Pin the version of the language and its package manager in the project's version files, and add their install to `.github/workflows/check.yml`.
2. If the package manager can reject versions released less than 7 days ago, turn that on. In CI, install from the lockfile in the mode that fails when the lockfile is missing or out of date.
3. Add the formatter and the linter to `TOOLS` in `scripts/check`, which runs them on the changed files. Add the type checker and the command that runs the tests to `TESTS`, which runs them on the whole project, because a change can break code in files it doesn't touch. Leave `scripts/` out of the type checker and the test command, because `scripts/check` tests and type-checks the loop's scripts itself. Turn on these settings where the tools have them, for the tests as well as the code:
   - the type checker's strict mode, or the compiler's setting that turns warnings into errors
   - rules against unused code
   - rules against code that ignores an error, such as an empty `catch` block or a promise that nothing awaits
   - checks against blanket or unused suppression comments
   - a failure on a focused test, such as `test.only`
4. Count each function's cognitive complexity with a tool that follows Sonar's definition, such as the linter's own rule. Add it to `scripts/check` twice, with the same two limits as the Python entries there: in `TOOLS` to fail the check, and in `REVIEW_TOOLS` to show reviewers what to judge.
5. For each setting you turned on, write a small example that breaks it, and see `scripts/check` fail. Delete the examples before you commit.
6. Add the package manager to `.github/dependabot.yml`.
7. Add the language's install, run and test commands to "Commands" in AGENTS.md.

## Add a dependency or a tool

- Add a dependency only when it saves more work than it costs to review, update and secure. Prefer the standard library.
- Use the newest stable version that is at least 7 days old.
- Record the exact version of everything the project installs: packages in a committed lockfile, and a tool outside it with its version and checksum in the file that installs it.
- Name each package you added or updated in the pull request. Leave routine updates to Dependabot.
