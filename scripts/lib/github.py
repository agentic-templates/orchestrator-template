"""Calls to GitHub through `gh`, shared by the loop's scripts."""

import json
import subprocess
import sys
from functools import cache

WRITE_ACCESS = ("admin", "maintain", "write")


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def gh_json(*args: str) -> object:
    output = gh(*args)
    return json.loads(output) if output else None


@cache
def repository() -> str:
    return gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")


@cache
def is_maintainer(login: str) -> bool:
    """Whether the account has write access to the repository, as AGENTS.md defines a maintainer."""
    permission = gh_json("api", f"repos/{repository()}/collaborators/{login}/permission")
    return permission["permission"] in WRITE_ACCESS


def open_blockers(issue: int) -> list[int]:
    blockers = gh_json("api", f"repos/{repository()}/issues/{issue}/dependencies/blocked_by") or []
    return [blocker["number"] for blocker in blockers if blocker["state"] == "open"]
