"""A base for tests that run the loop's scripts and hooks, with the loop's files in a temporary folder."""

import io
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import github
import refreshing
import usage
import watching

SCRIPTS = Path(__file__).resolve().parents[1]
# Variables that Claude Code, scripts/launch, scripts/orchestrate and tmux set, which change what the scripts do. A
# test that runs inside an agent's or the orchestrator's session would otherwise inherit them.
SESSION_VARIABLES = ("CLAUDE_PID", "CLAUDE_CONFIG_DIR", "LOOP_AGENT_ID", "LOOP_ORCHESTRATE", "TMUX", "TMUX_PANE")


class TemporaryLoopTest(unittest.TestCase):
    """Points every path under .loop in usage, refreshing and watching at a temporary .loop, and usage.ROOT at the
    folder that holds it, so that a script under test can't read or change the real loop or start its scripts.
    The session-start hook finds scripts/status from a root of its own, which this doesn't patch, so a test that
    runs the hook's status part would run the real scripts/status."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.loop = self.root / ".loop"
        self.loop.mkdir()
        real_loop = usage.LOOP
        patches = [mock.patch.object(usage, "ROOT", self.root)]
        for module in (usage, refreshing, watching):
            for name, path in vars(module).items():
                if isinstance(path, Path) and path.is_relative_to(real_loop):
                    patches.append(mock.patch.object(module, name, self.loop / path.relative_to(real_loop)))
        patches.append(mock.patch.dict(os.environ))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        for variable in SESSION_VARIABLES:
            os.environ.pop(variable, None)
        # A tmux command that a script under test runs then finds no server, rather than the maintainer's.
        os.environ["TMUX_TMPDIR"] = str(self.root)
        # github caches gh's answers for the whole run of a script, and each test stands for a run of its own.
        for function in vars(github).values():
            if hasattr(function, "cache_clear"):
                function.cache_clear()

    def run_script(self, script: str, *arguments: str, stdin: str = "") -> tuple[int, str]:
        """Runs scripts/<script> in the test's process, with the test's patches, and gives its exit status, from the
        SystemExit it raises, and what it printed to stdout."""
        output = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            mock.patch("sys.argv", [script, *arguments]),
            redirect_stdout(output),
        ):
            try:
                runpy.run_path(str(SCRIPTS / script), run_name="__main__")
            except SystemExit as exit:
                # Python exits with 1 when a script exits with a message.
                return 1 if isinstance(exit.code, str) else int(exit.code or 0), output.getvalue()
        return 0, output.getvalue()

    def start_process(self, name: str) -> int:
        """A process whose command line names it as `name`, as a running script's does."""
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", name])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process.pid

    def load_script(self, script: str) -> dict:
        """The functions and constants of scripts/<script>, loaded without running it."""
        return runpy.run_path(str(SCRIPTS / script))

    def stand_in(self, program: str, script: str) -> None:
        """Puts a shell script first on PATH as the program. It runs from the temporary folder."""
        folder = self.root / "bin"
        folder.mkdir(exist_ok=True)
        path = folder / program
        path.write_text(f"#!/bin/sh\ncd {self.root}\n{script}\n")
        path.chmod(0o755)
        os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ['PATH']}"


class Clock:
    """A stand-in for the clock, which moves only when the code sleeps."""

    def __init__(self, now: float):
        self.now = now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def patch(self, test: unittest.TestCase) -> None:
        for name in ("time", "sleep"):
            patch = mock.patch(f"time.{name}", getattr(self, name))
            patch.start()
            test.addCleanup(patch.stop)
