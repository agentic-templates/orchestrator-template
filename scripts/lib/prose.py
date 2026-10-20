"""The rules of the `writing` skill that a script can check, applied to Markdown text."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    line: int
    message: str


@dataclass(frozen=True)
class Line:
    number: int
    text: str
    kind: str  # "prose", "list", "table" or "heading"


WORD_ADVICE = {
    "simply": 'Cut "simply".',
    "just": 'Cut "just".',
    "basically": 'Cut "basically".',
    "note that": 'Cut "note that" and say the thing itself.',
    "seamless": 'Replace "seamless" with the fact behind it.',
    "seamlessly": 'Replace "seamlessly" with the fact behind it.',
    "powerful": 'Replace "powerful" with the fact behind it.',
    "blazing": 'Replace "blazing" with a measured number.',
    "handles": 'Replace "handles" with the actual action.',
    "deals with": 'Replace "deals with" with the actual action.',
    "manages": 'Replace "manages" with the actual action.',
}
WORDS = re.compile(r"\b(" + "|".join(re.escape(word) for word in WORD_ADVICE) + r")\b", re.IGNORECASE)
JOINING_DASH = re.compile(r"\w\s*—\s*\w|[A-Za-z]\s+[–-]\s+[A-Za-z]")
VAGUE_LINK = re.compile(r"\[(here|click here|this|this link|link)\]\(", re.IGNORECASE)
INLINE_CODE = re.compile(r"`[^`]*`")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
QUOTED = re.compile(r'"[^"]*"|\u201c[^\u201d]*\u201d')
LIST_MARKER = re.compile(r"^\s*([-*+]|\d+\.)\s+")


def lines(text: str) -> list[Line]:
    """The lines of a Markdown text that hold words, without front matter, code and link targets."""
    text = HTML_COMMENT.sub(lambda match: "\n" * match.group().count("\n"), text)
    result = []
    in_code = False
    in_front_matter = text.startswith("---\n")
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if in_front_matter:
            if number > 1 and stripped == "---":
                in_front_matter = False
            continue
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        # Quoted text, such as an example of what not to write, isn't held to the rules.
        cleaned = QUOTED.sub("quote", URL.sub("", LINK.sub(r"\1", INLINE_CODE.sub("code", raw))))
        if stripped.startswith("|"):
            kind = "table"
        elif stripped.startswith("#"):
            kind = "heading"
        elif LIST_MARKER.match(raw):
            kind = "list"
            cleaned = LIST_MARKER.sub("", cleaned)
        else:
            kind = "prose"
        result.append(Line(number, cleaned, kind))
    return result


def check(text: str) -> list[Problem]:
    """The places where the text breaks a rule of the `writing` skill."""
    problems = [
        Problem(text.count("\n", 0, match.start()) + 1, "Make the link text name the page it opens.")
        for match in VAGUE_LINK.finditer(text)
    ]
    for line in lines(text):
        if ";" in line.text and line.kind != "table":
            problems.append(Problem(line.number, "Write two sentences instead of joining them with a semicolon."))
        if JOINING_DASH.search(line.text):
            problems.append(
                Problem(line.number, "Write two sentences, or use a comma, instead of joining with a dash.")
            )
        for match in WORDS.finditer(line.text):
            problems.append(Problem(line.number, WORD_ADVICE[match.group(1).lower()]))
    return problems


def prose_words(text: str) -> int:
    """The number of words in the text's sentences, leaving out headings, tables and code."""
    return sum(len(line.text.split()) for line in lines(text) if line.kind in ("prose", "list"))
