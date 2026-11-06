import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple

from temporary_loop import SCRIPTS

DELETED_FILES = ("deleted-in-a-commit.txt", "deleted-in-the-folder.txt")


class Run(NamedTuple):
    """What a run of scripts/check gave: its exit status, what it printed, and the commands the stand-ins got."""

    status: int
    output: str
    commands: list[str]
    root: Path

    def files_given_to(self, tool: str) -> list[list[str]]:
        """For each command that names the tool, such as "typos@", which every file goes to, every word of the command
        that is a file in the repository."""
        return [
            [word for word in command.split() if (self.root / word).is_file()]
            for command in self.commands
            if tool in command
        ]


class Repository:
    """A new git repository in a temporary folder, with a copy of scripts/check. Stand-ins for uv, which runs the
    loop's tests, and uvx, which runs every other tool, record each command they get. Git and scripts/check run with
    the repository's own environment, so the tests leave their own process's environment as it was."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.root = folder / "repository"
        (self.root / "scripts" / "lib").mkdir(parents=True)
        shutil.copy(SCRIPTS / "check", self.root / "scripts")
        for module in ("files.py", "prose.py"):
            shutil.copy(SCRIPTS / "lib" / module, self.root / "scripts" / "lib")
        # scripts/check's imports leave bytecode beside them, which the real repository ignores too.
        (self.root / ".gitignore").write_text("__pycache__/\n")
        self.environment = os.environ.copy()
        # The tests of scripts/check run inside scripts/check, which sets PYTHONSAFEPATH for them.
        self.environment.pop("PYTHONSAFEPATH", None)
        self.environment |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        for role in ("AUTHOR", "COMMITTER"):
            self.environment |= {f"GIT_{role}_NAME": "Test", f"GIT_{role}_EMAIL": "test@example.com"}
        self.environment["PATH"] = f"{folder / 'bin'}{os.pathsep}{os.environ['PATH']}"
        self.record_commands()
        self.git("init", "--quiet", "--initial-branch=main")

    def record_commands(self) -> None:
        """Makes uv and uvx stand-ins that record each command and succeed."""
        self.stand_in("uv", 'echo "uv $* PYTHONSAFEPATH=$PYTHONSAFEPATH" >> commands')
        self.stand_in("uvx", 'echo "uvx $*" >> commands')

    def stand_in(self, program: str, script: str) -> None:
        """Puts a shell script first on the repository's PATH as the program. It runs from the temporary folder."""
        path = self.folder / "bin" / program
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"#!/bin/sh\ncd {self.folder}\n{script}\n")
        path.chmod(0o755)

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=self.root, env=self.environment, check=True, capture_output=True)

    def write(self, path: str, text: str = "words\n") -> None:
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(text)

    def commit(self, message: str) -> None:
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", message)

    def check(self, *arguments: str) -> Run:
        result = subprocess.run(
            [sys.executable, "scripts/check", *arguments],
            check=False,
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
        )
        commands = self.folder / "commands"
        recorded = commands.read_text().splitlines() if commands.exists() else []
        commands.unlink(missing_ok=True)
        return Run(result.returncode, result.stdout + result.stderr, recorded, self.root)


def temporary_repository(test: type[unittest.TestCase]) -> Repository:
    folder = tempfile.TemporaryDirectory()
    test.addClassCleanup(folder.cleanup)
    return Repository(Path(folder.name))


class ExitStatusTest(unittest.TestCase):
    """A repository whose files break no rule, checked with --all, so that it needs no commit."""

    @classmethod
    def setUpClass(cls):
        cls.repository = temporary_repository(cls)
        cls.repository.write("notes.txt")
        cls.repository.git("add", "--all")

    def setUp(self):
        self.repository.record_commands()

    def test_fails_when_a_tool_fails(self):
        self.repository.stand_in("uvx", 'echo "uvx $*" >> commands\ncase "$*" in *typos@*) exit 1;; esac')
        self.assertEqual(self.repository.check("--all").status, 1)

    def test_fails_when_the_loop_tests_fail(self):
        self.repository.stand_in("uv", "exit 1")
        self.assertEqual(self.repository.check("--all").status, 1)


class CheckTest(unittest.TestCase):
    """One repository that holds every test's case, checked three times: plainly, with --base and with --all. A run per
    test cost 2 CPU seconds, so add a new case to this repository rather than a run of its own. main's
    first commit holds a loop script without .py that runs python3. The branch left from that commit, and main has a
    later commit that the branch doesn't have. The branch's one commit adds a file and deletes another. Besides a
    staged, a changed and an untracked file and a file deleted only from the folder, the untracked files are a Markdown
    file that breaks a rule and has a letter outside ASCII in its name, a workflow, Dependabot's settings, a composite
    action, an action.yml outside .github/actions/ and a new loop script."""

    @classmethod
    def setUpClass(cls):
        repository = temporary_repository(cls)
        repository.write("tracked.txt")
        repository.write("shared.txt")
        repository.write("scripts/tool", "#!/usr/bin/env python3\nTOTAL = 1\n")
        # Text of their own, so that git doesn't take a deleted file and an added one for a renamed file, which git diff
        # --name-only gives by its new name only.
        for path in DELETED_FILES:
            repository.write(path, f"{path}\n")
        repository.commit("the loop's scripts and files on main")
        repository.write("shared.txt", "changed on main\n")
        repository.git("commit", "--quiet", "--all", "--message", "a later change on main")
        repository.git("update-ref", "refs/remotes/origin/main", "HEAD")
        repository.git("switch", "--quiet", "--create", "branch", "HEAD~1")
        repository.write("committed.txt")
        (repository.root / "deleted-in-a-commit.txt").unlink()
        repository.commit("the branch's change")
        repository.write("staged.txt")
        repository.git("add", "staged.txt")
        repository.write("tracked.txt", "changed\n")
        repository.write("untracked.txt")
        (repository.root / "deleted-in-the-folder.txt").unlink()
        repository.write("docs/café.md", "It simply works.\n")
        repository.write(".github/workflows/extra.yaml")
        repository.write(".github/dependabot.yml")
        repository.write(".github/actions/setup/action.yml")
        repository.write("docs/action.yml")
        repository.write("scripts/lib/new.py", "TOTAL = 1\n")
        cls.plain = repository.check()
        cls.with_base = repository.check("--base", "HEAD~1")
        cls.every_file = repository.check("--all")

    def test_checks_the_commits_on_the_branch_and_uncommitted_files_but_not_later_commits_on_main(self):
        self.assertEqual(
            self.plain.files_given_to("typos@"),
            [
                [
                    ".github/actions/setup/action.yml",
                    ".github/dependabot.yml",
                    ".github/workflows/extra.yaml",
                    "committed.txt",
                    "docs/action.yml",
                    "docs/café.md",
                    "scripts/lib/new.py",
                    "staged.txt",
                    "tracked.txt",
                    "untracked.txt",
                ]
            ],
        )

    def test_with_base_checks_only_the_commits_since_the_base(self):
        self.assertEqual(self.with_base.files_given_to("typos@"), [["committed.txt"]])

    def test_fails_when_a_file_breaks_a_rule(self):
        self.assertEqual(self.plain.status, 1)
        self.assertIn('docs/café.md:1: Cut "simply".', self.plain.output)

    def test_checks_a_workflow_named_yaml_as_a_workflow(self):
        self.assertEqual(self.plain.files_given_to("actionlint"), [[".github/workflows/extra.yaml"]])
        self.assertIn([".github/workflows/extra.yaml"], self.plain.files_given_to("zizmor@"))

    def test_audits_dependabots_settings_and_composite_actions_for_security(self):
        files = [".github/actions/setup/action.yml", ".github/dependabot.yml"]
        self.assertIn(files, self.plain.files_given_to("zizmor@"))

    def test_keeps_an_action_yml_outside_github_actions_from_zizmor(self):
        audited = [path for paths in self.plain.files_given_to("zizmor@") for path in paths]
        self.assertNotIn("docs/action.yml", audited)

    def test_leaves_out_deleted_files_without_crashing(self):
        # files_given_to leaves out paths that aren't files, so this reads the commands themselves.
        for name, run in (("plain", self.plain), ("--base", self.with_base), ("--all", self.every_file)):
            with self.subTest(run=name):
                self.assertNotIn("Traceback", run.output)
                words = {word for command in run.commands for word in command.split()}
                self.assertFalse(words & set(DELETED_FILES), run.commands)
        # Only the plain run checks docs/café.md, which breaks a rule.
        self.assertEqual(self.with_base.status, 0, self.with_base.output)
        self.assertEqual(self.every_file.status, 0, self.every_file.output)

    def test_type_checks_every_loop_script_changed_or_not(self):
        [ty_files] = self.plain.files_given_to("ty@")
        self.assertCountEqual(
            ty_files,
            ["scripts/check", "scripts/lib/files.py", "scripts/lib/new.py", "scripts/lib/prose.py", "scripts/tool"],
        )

    def test_runs_the_loop_tests_without_the_current_folder_on_the_import_path(self):
        tests = next(command for command in self.plain.commands if command.startswith("uv run"))
        self.assertIn("PYTHONSAFEPATH=1", tests)
