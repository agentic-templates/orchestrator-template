"""Rules for the files in the repository that no linter covers."""

import re
from pathlib import PurePosixPath

from prose import Problem

HOME_PATH = re.compile(r"(/Users/|/home/|C:\\Users\\)[A-Za-z0-9._-]+")
# An address may start at any character of a run of the characters it may hold. Without the second alternative, the
# search starts again at each character of a long run without an address, such as a hash in a lock file. That takes
# 1.03 CPU seconds on a run of 20,000 characters and 4.15 on one of 40,000. The second alternative takes in such a run
# whole, so the search moves past it.
EMAIL_OR_RUN = re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b|[\w.+-]+")
# A clone address over SSH, such as git@github.com, has the form of an email address.
PUBLIC_EMAIL = re.compile(r"^git@|@(users\.noreply\.github\.com|example\.(com|org|net))$", re.IGNORECASE)
# Spelled in parts, because this rule applies to this file too.
UNFINISHED = re.compile(r"\b(TO" + r"DO|FIX" + r"ME|XX" + r"X)\b")
# Comments and attributes that make a linter or a type checker skip code, and the calls and attributes that skip a
# test, in the common languages. Each must give its reason, with the word "because", on its own line or the line above.
SUPPRESSION = re.compile(
    r"(?i:#\s*((ruff|flake8):\s*)?noqa\b)|#\s*ruff:\s*disable|#\s*zizmor:\s*ignore"
    r"|#\s*type:\s*ignore|#\s*ty:\s*ignore|#\s*pyright:\s*ignore|#\s*pylint:\s*disable|eslint-disable"
    r"|@ts-(ignore|expect-error|nocheck)|biome-ignore|//\s*nolint|#!?\[allow\(|@SuppressWarnings"
    r"|rubocop:disable|swiftlint:disable|NOLINT|#\s*complexipy:\s*ignore|#\s*shellcheck\s.*\bdisable="
    r"|^\s*@((unittest\.)?skip(If|Unless)?|Disabled\w*|Ignore)\b|\.skipTest\(|\bSkipTest\("
    r"|\bpytest\.(mark\.skip(if)?\b|importorskip\(|skip\()"
    # A JavaScript test marker counts only before .each, a test name in quotes or the end of the line, because a
    # Rust iterator named it has a skip that takes a number. Playwright's test.skip and test.fixme count before any
    # argument, because inside a test they take a condition.
    r"|(?<![\w.$])((x(it|test|describe)|(it|test|describe)\.skip)(\.each\b|\(\s*([\"'`]|$))"
    r"|(it|test|describe)\.skipIf\(|test\.(skip|fixme)\(|this\.skip\(\))"
    r"|((?<![\w.])[tb]|\bT\(\))\.Skip(f|Now)?\(|#\[ignore\b|\bXCTSkip(If|Unless)?\(|\[\w+\([^\]]*\bSkip\s*="
)
REASON = re.compile(r"\bbecause\b", re.IGNORECASE)
# Claude Code cuts the context that a hook injects at 10,000 characters.
MAX_HOOK_CHARACTERS = 10_000
# Codex reads at most 32 KiB of AGENTS.md.
MAX_AGENTS_BYTES = 32 * 1024


def check_text(path: str, text: str) -> list[Problem]:
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        if HOME_PATH.search(line):
            problems.append(Problem(number, "Remove the local path. Paths in a home folder are private."))
        for address in email_addresses(line):
            if not PUBLIC_EMAIL.search(address):
                problems.append(Problem(number, f"Remove the email address {address}."))
        if marker := UNFINISHED.search(line):
            advice = "Finish the work or leave it out, and list what's left in your report."
            problems.append(Problem(number, f'"{marker.group()}" marks unfinished work. {advice}'))
    return problems + check_suppressions(path, text) + check_size(path, text)


def email_addresses(line: str) -> list[str]:
    return [match.group() for match in EMAIL_OR_RUN.finditer(line) if "@" in match.group()]


def check_suppressions(path: str, text: str) -> list[Problem]:
    # Suppressions live in code. Docs mention them, and this file names them all in its pattern.
    if path.endswith(".md") or path == "scripts/lib/files.py":
        return []
    lines = text.splitlines()
    return [
        Problem(
            number, 'Give the reason for skipping the check, with the word "because", on this line or the one above.'
        )
        for number, line in enumerate(lines, start=1)
        if SUPPRESSION.search(line)
        and not REASON.search(line)
        and not (number > 1 and REASON.search(lines[number - 2]))
    ]


def check_size(path: str, text: str) -> list[Problem]:
    parts = PurePosixPath(path).parts
    if parts[:2] == (".agents", "skills") and len(text) > MAX_HOOK_CHARACTERS:
        message = (
            f"This skill has {len(text):,} characters, and a skill may have at most {MAX_HOOK_CHARACTERS:,}, the most "
            "a hook can inject. Cut it, or move the rules that only some kinds of work need into a skill of their own."
        )
        return [Problem(1, message)]
    if path == "AGENTS.md" and len(text.encode()) > MAX_AGENTS_BYTES:
        message = (
            f"AGENTS.md has {len(text.encode()):,} bytes, and Codex reads only the first {MAX_AGENTS_BYTES:,}. Move "
            "rules into the skills they belong to."
        )
        return [Problem(1, message)]
    return []


def check_name(path: str) -> list[Problem]:
    if PurePosixPath(path).name in ("CLAUDE.md", "CLAUDE.local.md"):
        return [Problem(1, "Delete this file. Claude Code reads AGENTS.md only when no CLAUDE.md exists.")]
    return []
