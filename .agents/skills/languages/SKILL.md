---
name: languages
description: How to add a language, a tool or a dependency to the project, with the strict settings each language's tools need. Read it before you add one.
---

# Languages and dependencies

## Add a language

1. Pin the version of the language and its package manager in the project's version files, and add their install to `.github/workflows/check.yml`.
2. If the package manager can reject versions released less than 7 days ago, turn that on. In CI, install from the lockfile in the mode that fails when the lockfile is missing or out of date.
3. Add the formatter, the linter, the type checker if the language has one, and the test command to `scripts/check`, so that each runs on the changed files only. Turn on these settings where the tools have them, for the tests as well as the code:
   - the type checker's strict mode, or the compiler's setting that turns warnings into errors
   - rules against unused code
   - rules against code that ignores an error, such as an empty `catch` block or a promise that nothing awaits
   - checks against blanket or unused suppression comments
   - a failure on a focused test, such as `test.only`
   - a limit on how complex one function can be: the cognitive count where the linter has one, with a limit of 15, or Ruff's C901 with a limit of 10
4. For each setting you turned on, write a small example that breaks it, and see `scripts/check` fail. Delete the examples before you commit.
5. Add the package manager to `.github/dependabot.yml`.
6. Add the language's install, run and test commands to "Commands" in AGENTS.md.

## Add a dependency or a tool

- Add a dependency only when it saves more work than it costs to review, update and secure. Prefer the standard library.
- Use the newest stable version that is at least 7 days old.
- Record the exact version of everything the project installs: packages in a committed lockfile, and a tool outside it with its version and checksum in the file that installs it.
- Name each package you added or updated in the pull request. Leave routine updates to Dependabot.
