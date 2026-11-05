import os
import shutil
import subprocess
import sys

from temporary_loop import SCRIPTS, TemporaryLoopTest


class CheckTest(TemporaryLoopTest):
    """Runs a copy of scripts/check in a temporary repository whose origin/main is its first commit. Stand-ins for uv,
    which runs the loop's tests, and uvx, which runs every other tool, record each command they get."""

    def setUp(self):
        super().setUp()
        self.repository = self.root / "repository"
        (self.repository / "scripts" / "lib").mkdir(parents=True)
        shutil.copy(SCRIPTS / "check", self.repository / "scripts")
        for module in ("files.py", "prose.py"):
            shutil.copy(SCRIPTS / "lib" / module, self.repository / "scripts" / "lib")
        # The tests of scripts/check run inside scripts/check, which sets PYTHONSAFEPATH for them.
        os.environ.pop("PYTHONSAFEPATH", None)
        os.environ |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        for role in ("AUTHOR", "COMMITTER"):
            os.environ |= {f"GIT_{role}_NAME": "Test", f"GIT_{role}_EMAIL": "test@example.com"}
        self.git("init", "--quiet", "--initial-branch=main")
        self.commit("the loop's scripts")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.stand_in("uv", 'echo "uv $* PYTHONSAFEPATH=$PYTHONSAFEPATH" >> commands')
        self.stand_in("uvx", 'echo "uvx $*" >> commands')

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=self.repository, check=True, capture_output=True, text=True
        ).stdout

    def write(self, path: str, text: str = "words\n") -> None:
        (self.repository / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repository / path).write_text(text)

    def commit(self, message: str) -> None:
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", message)

    def check(self, *arguments: str) -> tuple[int, str]:
        """Runs scripts/check, and gives its exit status and what it printed."""
        result = subprocess.run(
            [sys.executable, "scripts/check", *arguments],
            check=False,
            cwd=self.repository,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    def files_given_to(self, tool: str) -> list[str]:
        """The files at the end of the command that names the tool, such as "typos@", which every file goes to."""
        commands = (self.root / "commands").read_text().splitlines()
        command = next(command for command in commands if tool in command)
        return [word for word in command.split() if (self.repository / word).is_file()]


class ExitStatusTest(CheckTest):
    def test_fails_when_a_tool_fails(self):
        self.stand_in("uvx", 'echo "uvx $*" >> commands\ncase "$*" in *typos@*) exit 1;; esac')
        self.write("notes.txt")
        self.assertEqual(self.check()[0], 1)

    def test_fails_when_the_loop_tests_fail(self):
        self.stand_in("uv", "exit 1")
        self.write("notes.txt")
        self.assertEqual(self.check()[0], 1)

    def test_fails_when_a_file_breaks_a_rule(self):
        self.write("docs/guide.md", "It simply works.\n")
        status, output = self.check()
        self.assertEqual(status, 1)
        self.assertIn('docs/guide.md:1: Cut "simply".', output)


class ChangedFilesTest(CheckTest):
    def setUp(self):
        super().setUp()
        self.write("tracked.txt")
        self.write("shared.txt")
        self.commit("files on main")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("switch", "--quiet", "--create", "branch")
        self.write("committed.txt")
        self.commit("the branch's change")
        # main moves on after the branch left it.
        self.git("switch", "--quiet", "main")
        self.write("shared.txt", "changed on main\n")
        self.commit("a later change on main")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("switch", "--quiet", "branch")
        self.write("staged.txt")
        self.git("add", "staged.txt")
        self.write("tracked.txt", "changed\n")
        self.write("untracked.txt")

    def test_checks_the_commits_on_the_branch_and_uncommitted_files_but_not_later_commits_on_main(self):
        self.check()
        self.assertEqual(self.files_given_to("typos@"), ["committed.txt", "staged.txt", "tracked.txt", "untracked.txt"])

    def test_with_base_checks_only_the_commits_since_the_base(self):
        self.check("--base", self.git("rev-parse", "branch~1").strip())
        self.assertEqual(self.files_given_to("typos@"), ["committed.txt"])


class FileNamesTest(CheckTest):
    def test_checks_a_file_whose_name_has_a_letter_outside_ascii(self):
        self.write("docs/café.md", "It simply works.\n")
        self.assertIn('docs/café.md:1: Cut "simply".', self.check()[1])


class DeletedFilesTest(CheckTest):
    def test_a_branch_that_deletes_files_passes(self):
        self.write("deleted-in-a-commit.txt")
        self.write("deleted-in-the-folder.txt")
        self.commit("files on main")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("rm", "--quiet", "deleted-in-a-commit.txt")
        self.commit("a deletion")
        (self.repository / "deleted-in-the-folder.txt").unlink()
        self.write("notes.txt")
        for arguments in ((), ("--all",)):
            with self.subTest(arguments=arguments):
                status, output = self.check(*arguments)
                self.assertEqual(status, 0, output)


class LoopScriptsTest(CheckTest):
    def test_type_checks_every_loop_script_changed_or_not(self):
        self.write("notes.txt")
        self.check()
        self.assertEqual(self.files_given_to("ty@"), ["scripts/check", "scripts/lib/files.py", "scripts/lib/prose.py"])

    def test_type_checks_a_new_loop_script_before_it_is_committed(self):
        self.write("scripts/lib/new.py", "TOTAL = 1\n")
        self.check()
        self.assertIn("scripts/lib/new.py", self.files_given_to("ty@"))

    def test_runs_the_loop_tests_without_the_current_folder_on_the_import_path(self):
        self.write("notes.txt")
        self.check()
        commands = (self.root / "commands").read_text().splitlines()
        self.assertIn("PYTHONSAFEPATH=1", next(command for command in commands if command.startswith("uv run")))
