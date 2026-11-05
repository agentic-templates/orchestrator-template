"""Rules for the files in the repository that no linter covers."""

import re
from pathlib import PurePosixPath

from prose import Problem

HOME_PATH = re.compile(r"(/Users/|/home/|C:\\Users\\)[A-Za-z0-9._-]+")
# An address starts only where a run of the characters it may hold starts. Otherwise the search starts again at each
# character of a long run, such as a hash in a lock file, and 30,000 letters took 2.3 CPU seconds.
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# A clone address over SSH, such as git@github.com, has the form of an email address.
PUBLIC_EMAIL = re.compile(r"^git@|@(users\.noreply\.github\.com|example\.(com|org|net))$", re.IGNORECASE)
# Spelled in parts, because this rule applies to this file too.
UNFINISHED = re.compile(r"\b(TO" + r"DO|FIX" + r"ME|XX" + r"X)\b")
# Comments and attributes that make a linter or a type checker skip code, in the common languages.
# Each must give its reason, starting with "because", on its own line or the line above.
SUPPRESSION = re.compile(
    r"(?i:#\s*((ruff|flake8):\s*)?noqa\b)|#\s*ruff:\s*disable|#\s*zizmor:\s*ignore"
    r"|#\s*type:\s*ignore|#\s*ty:\s*ignore|#\s*pyright:\s*ignore|#\s*pylint:\s*disable|eslint-disable"
    r"|@ts-(ignore|expect-error|nocheck)|biome-ignore|//\s*nolint|#!?\[allow\(|@SuppressWarnings"
    r"|rubocop:disable|swiftlint:disable|NOLINT|#\s*complexipy:\s*ignore"
)
REASON = re.compile(r"\bbecause\b", re.IGNORECASE)
# Claude Code cuts a hook's output at 10,000 characters. The session-start hook cuts what it injects to fit, and no
# skill may be longer. test_skills.py checks that the orchestrator's skills fit together.
MAX_HOOK_CHARACTERS = 10_000
# Codex reads at most 32 KiB of AGENTS.md.
MAX_AGENTS_BYTES = 32 * 1024


def check_text(path: str, text: str) -> list[Problem]:
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        if HOME_PATH.search(line):
            problems.append(Problem(number, "Remove the local path. Paths in a home folder are private."))
        for match in EMAIL.finditer(line):
            if not PUBLIC_EMAIL.search(match.group()):
                problems.append(Problem(number, f"Remove the email address {match.group()}."))
        if UNFINISHED.search(line):
            problems.append(Problem(number, "Finish the work or leave it out, and list what's left in your report."))
    return problems + check_suppressions(path, text) + check_size(path, text)


def check_suppressions(path: str, text: str) -> list[Problem]:
    # Suppressions live in code. Docs mention them, and this file names them all in its pattern.
    if path.endswith(".md") or path == "scripts/lib/files.py":
        return []
    lines = text.splitlines()
    return [
        Problem(number, 'Give the reason for skipping the check in the comment, starting with "because".')
        for number, line in enumerate(lines, start=1)
        if SUPPRESSION.search(line)
        and not REASON.search(line)
        and not (number > 1 and REASON.search(lines[number - 2]))
    ]


def check_size(path: str, text: str) -> list[Problem]:
    parts = PurePosixPath(path).parts
    if parts[:2] == (".agents", "skills") and len(text) > MAX_HOOK_CHARACTERS:
        return [Problem(1, "This skill is too long to inject in full. Cut it, or split it by when it's needed.")]
    if path == "AGENTS.md" and len(text.encode()) > MAX_AGENTS_BYTES:
        return [
            Problem(1, "AGENTS.md is too long for Codex to read in full. Move rules into the skills they belong to.")
        ]
    return []


def check_name(path: str) -> list[Problem]:
    if PurePosixPath(path).name in ("CLAUDE.md", "CLAUDE.local.md"):
        return [Problem(1, "Delete this file. Claude Code reads AGENTS.md only when no CLAUDE.md exists.")]
    return []
