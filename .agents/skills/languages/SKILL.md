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
4. Limit each function's cognitive complexity to 15, as Sonar counts it, so that the limit means the same in every language. If the language's linter can't count it, add the tool from this table to `scripts/check`:

   | Language | Tool and setting |
   |---|---|
   | Python | `complexipy --max-complexity-allowed 15` |
   | JavaScript, TypeScript | Biome's `noExcessiveCognitiveComplexity`, or ESLint's `sonarjs/cognitive-complexity` |
   | Go | `gocognit -over 15` |
   | Rust | Clippy's `cognitive_complexity`, with `cognitive-complexity-threshold = 15` |
   | Java | PMD's `CognitiveComplexity` |
   | Kotlin | detekt's `CognitiveComplexMethod` |
   | C, C++ | clang-tidy's `readability-function-cognitive-complexity` |

   For a language that no tool counts it for, limit the cyclomatic count to 10 with `lizard`.
5. For each setting you turned on, write a small example that breaks it, and see `scripts/check` fail. Delete the examples before you commit.
6. Add the package manager to `.github/dependabot.yml`.
7. Add the language's install, run and test commands to "Commands" in AGENTS.md.

## Add a dependency or a tool

- Add a dependency only when it saves more work than it costs to review, update and secure. Prefer the standard library.
- Use the newest stable version that is at least 7 days old.
- Record the exact version of everything the project installs: packages in a committed lockfile, and a tool outside it with its version and checksum in the file that installs it.
- Name each package you added or updated in the pull request. Leave routine updates to Dependabot.
