"""Rules for the files in the repository that no linter covers."""

import re
from pathlib import PurePosixPath

from prose import Problem

HOME_PATH = re.compile(r"(/Users/|/home/|C:\\Users\\)[A-Za-z0-9._-]+")
EMAIL = re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
PUBLIC_EMAIL = re.compile(r"@(users\.noreply\.github\.com|example\.(com|org|net))$", re.IGNORECASE)
# Spelled in two parts, so that this file doesn't break its own rule.
UNFINISHED = re.compile(r"\b(TO" + r"DO|FIX" + r"ME|XX" + r"X)\b")
# Comments and attributes that make a linter or a type checker skip code, in the common languages.
# Each must give its reason, starting with "because", on its own line or the line above.
SUPPRESSION = re.compile(
    r"#\s*no" + r"qa\b|#\s*type:\s*ignore|#\s*pyright:\s*ignore|#\s*pylint:\s*disable|eslint-disable"
    r"|@ts-(ignore|expect-error|nocheck)|biome-ignore|//\s*nolint|#!?\[allow\(|@SuppressWarnings"
    r"|rubocop:disable|swiftlint:disable|NOLINT|#\s*complexipy:\s*ignore"
)
REASON = re.compile(r"\bbecause\b", re.IGNORECASE)
# A hook can inject at most 10,000 characters, and Codex reads at most 32 KiB of AGENTS.md.
MAX_SKILL_CHARACTERS = 10_000
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
    if parts[:2] == (".agents", "skills") and len(text) > MAX_SKILL_CHARACTERS:
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
