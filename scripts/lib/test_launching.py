import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import launching
import meter
import usage

CLAUDE = usage.Account("claude", "claude", None)
CLAUDE_2 = usage.Account("claude-2", "claude", "~/.claude-2")
ACCOUNTS = [CLAUDE, CLAUDE_2, usage.Account("codex", "codex", None)]


def decisions(claude_room: int, claude_2_room: int, codex_room: int = 5) -> dict[str, meter.Decision]:
    def decision(room: int) -> meter.Decision:
        return meter.Decision("start" if room else "hold", room, 0, 40.0, 0.1)

    return {"claude": decision(claude_room), "claude-2": decision(claude_2_room), "codex": decision(codex_room)}


class ChooseAccountTest(unittest.TestCase):
    def test_a_requested_account_with_room_is_used(self):
        account, _ = launching.choose_account(decisions(1, 3), ACCOUNTS, "claude", force=False)
        self.assertEqual(account, CLAUDE)

    def test_a_requested_account_without_room_is_refused_with_the_meter_report(self):
        account, reason = launching.choose_account(decisions(0, 3), ACCOUNTS, "claude", force=False)
        self.assertIsNone(account)
        self.assertIn("without --account", reason)
        self.assertIn("Start the next agent on: claude-2", reason)

    def test_force_starts_on_a_requested_account_without_room(self):
        account, _ = launching.choose_account(decisions(0, 0, 0), ACCOUNTS, "claude", force=True)
        self.assertEqual(account, CLAUDE)

    def test_without_a_request_the_account_with_room_is_used(self):
        account, _ = launching.choose_account(decisions(0, 2), ACCOUNTS, None, force=False)
        self.assertEqual(account, CLAUDE_2)

    def test_without_a_request_an_account_the_launcher_cant_start_isnt_used(self):
        account, _ = launching.choose_account(decisions(0, 0, codex_room=5), ACCOUNTS, None, force=False)
        self.assertIsNone(account)

    def test_without_room_anywhere_nothing_starts(self):
        account, reason = launching.choose_account(decisions(0, 0, 0), ACCOUNTS, None, force=False)
        self.assertIsNone(account)
        self.assertIn("No account has room", reason)

    def test_an_account_that_the_launcher_cant_start_is_refused(self):
        account, reason = launching.choose_account(decisions(1, 3), ACCOUNTS, "codex", force=True)
        self.assertIsNone(account)
        self.assertIn("can't start agents there", reason)

    def test_an_unknown_account_is_refused(self):
        account, reason = launching.choose_account(decisions(1, 1), ACCOUNTS, "claude-9", force=False)
        self.assertIsNone(account)
        self.assertIn("No account is called claude-9", reason)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        # The maintainer's own file mustn't change what these tests see.
        patch = mock.patch.object(launching, "AGENTS_ENV", Path("/nonexistent/agents.env"))
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_sandbox_lets_an_agent_write_only_its_own_uv_cache(self):
        writable = json.loads(launching.settings())["sandbox"]["filesystem"]["allowWrite"]
        self.assertEqual(writable, [str(launching.UV_CACHE)])

    def test_an_agent_cant_run_a_command_outside_the_sandbox(self):
        self.assertIs(json.loads(launching.settings())["sandbox"]["allowUnsandboxedCommands"], False)

    def test_an_agent_cant_read_secrets_or_account_folders_but_can_read_what_gh_and_git_need(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = Path(folder.name)
        for name in ("gh", "git", "anthropic"):
            (home / ".config" / name).mkdir(parents=True)
        accounts = home / "accounts.json"
        accounts.write_text(json.dumps([{"name": "claude-2", "provider": "claude", "home": str(home / ".claude-2")}]))
        with mock.patch.object(Path, "home", lambda: home), mock.patch.object(launching.usage, "ACCOUNTS", accounts):
            settings = json.loads(launching.settings())
        denied = settings["permissions"]["deny"]
        expected = [
            "Read(~/.netrc)",
            "Read(~/.config/git/credentials)",
            "Read(~/.config/anthropic/**)",
            "Read(~/.claude-2/**)",
            "Read(~/.claude.json)",
            "Read(~/Library/Keychains/**)",
        ]
        for rule in expected:
            self.assertIn(rule, denied)
        self.assertFalse([rule for rule in denied if "/.config/gh" in rule or rule.startswith("Read(~/.config/git/**")])
        self.assertIn({"path": "~/.claude-2", "mode": "deny"}, settings["sandbox"]["credentials"]["files"])

    def test_an_agent_cant_read_or_edit_the_files_the_project_denies_in_the_main_checkout(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        main = Path(folder.name)
        (main / ".claude").mkdir()
        deny = [
            "Read(**/.env)",
            "Read(./.env.*)",
            "Read(/secrets/**)",
            "Read(keys/**)",
            "Read(key.pem)",
            "Read(~secrets)",
            "Read(~/.ssh/**)",
            "Edit(/.loop/agents.env)",
            "Bash(rm *)",
        ]
        (main / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": deny}, "sandbox": {}}))
        with mock.patch.object(launching, "ROOT", main):
            denied = json.loads(launching.settings())["permissions"]["deny"]
        in_main = [rule for rule in denied if rule.startswith((f"Read(/{main}/", f"Edit(/{main}/"))]
        expected = [
            f"Read(/{main}/{path})"
            for path in ("**/.env", "**/.env.*", "secrets/**", "**/keys/**", "**/key.pem", "**/~secrets")
        ]
        self.assertEqual(in_main, [*expected, f"Edit(/{main}/.loop/agents.env)"])


class AgentVariablesTest(unittest.TestCase):
    def test_lines_give_variables_and_the_hosts_they_name(self):
        text = (
            "# Keys for the summarizer\n"
            "\n"
            "DEEPSEEK_API_KEY=sk-1 # host: api.deepseek.com\n"
            "export MODE=fast # for the tests\n"
            'GREETING="hello # there" # host: greetings.example.com\n'
            "HASHED=a#b\n"
            "SPACED = two words\n"
            "EMPTY=\n"
            "UPLOAD_KEY='k-2'\t# host: *.uploads.example.com:8443\n"
        )
        variables, hosts = launching.parse_agent_variables(text)
        expected = {
            "DEEPSEEK_API_KEY": "sk-1",
            "MODE": "fast",
            "GREETING": "hello # there",
            "HASHED": "a#b",
            "SPACED": "two words",
            "EMPTY": "",
            "UPLOAD_KEY": "k-2",
        }
        self.assertEqual(variables, expected)
        self.assertEqual(hosts, ["api.deepseek.com", "greetings.example.com", "*.uploads.example.com:8443"])

    def test_a_line_it_cant_read_is_refused_by_its_number_without_its_value(self):
        for line in (
            "sk-secret",
            "KEY sk-secret",
            'KEY="sk-secret',
            'KEY="sk-secret\\nline"',
            "KEY=sk-secret # host:",
            "KEY=sk-secret # host: *",
            "KEY=sk-secret # host: https://api.example.com",
        ):
            with self.subTest(line), self.assertRaises(ValueError) as refusal:
                launching.parse_agent_variables(f"MODE=fast\n{line}\n")
            self.assertIn("line 2 ", str(refusal.exception).lower())
            self.assertNotIn("sk-secret", str(refusal.exception))

    def test_launches_are_refused_while_the_file_has_a_line_it_cant_read(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "agents.env"
        with mock.patch.object(launching, "AGENTS_ENV", path):
            self.assertEqual(launching.agent_variables_refusal(), "")
            path.write_text("KEY=sk-secret # host: https://api.example.com\n")
            self.assertIn("No agent can start", launching.agent_variables_refusal())


class WithoutSecretsTest(unittest.TestCase):
    def test_names_with_a_secret_word_are_left_out(self):
        environment = {
            name: "x"
            for name in (
                "GH_TOKEN", "OPENAI_API_KEY", "KEY", "aws_secret_access_key", "DB_PASSWORD",
                "GOOGLE_APPLICATION_CREDENTIALS", "PATH", "SSH_AUTH_SOCK", "KEYBOARD_LAYOUT", "TOKENIZERS_PARALLELISM",
            )
        }  # fmt: skip
        kept, left_out = launching.without_secrets(environment)
        self.assertEqual(sorted(kept), ["KEYBOARD_LAYOUT", "PATH", "SSH_AUTH_SOCK", "TOKENIZERS_PARALLELISM"])
        expected = [
            "DB_PASSWORD", "GH_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "KEY", "OPENAI_API_KEY", "aws_secret_access_key",
        ]  # fmt: skip
        self.assertEqual(left_out, expected)


class StartTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        agents_env = self.folder / "agents.env"
        agents_env.write_text("DEEPSEEK_API_KEY=from-file # host: api.deepseek.com\n")
        inherited = {"PATH": os.environ["PATH"], "OPENAI_API_KEY": "x", "DEEPSEEK_API_KEY": "x", "GH_TOKEN": "x"}
        # Stands in for Claude Code, which the test mustn't start.
        self.popen = mock.MagicMock()
        for patch in (
            mock.patch.dict(os.environ, inherited, clear=True),
            mock.patch.object(launching, "AGENTS_ENV", agents_env),
            mock.patch.object(launching.usage, "AGENTS", self.folder / "agents"),
            mock.patch.object(launching, "github_login", lambda: {"GH_TOKEN": "the-loops"}),
            mock.patch.object(launching.subprocess, "Popen", self.popen),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def start(self, resumes: bool) -> tuple[dict[str, str], str, list[str]]:
        """The agent's environment, its first message and its command."""
        with mock.patch.object(launching.usage, "transcript", lambda account, session: resumes):
            launching.start(ACCOUNTS[0], "builder", "builder-1-test", self.folder, "Build issue #1.", "session")
        reader = os.open(self.folder / "agents" / "builder-1-test.input", os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        message = json.loads(os.read(reader, 65536))["message"]["content"]
        command = self.popen.call_args.args[0]
        return self.popen.call_args.kwargs["env"], message, command

    def test_an_agent_gets_the_files_variables_but_not_inherited_secrets(self):
        environment, message, command = self.start(resumes=False)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertEqual(environment["DEEPSEEK_API_KEY"], "from-file")
        self.assertEqual(environment["GH_TOKEN"], "the-loops")
        self.assertTrue(message.startswith("Build issue #1.\n\n"))
        self.assertIn("leaves out OPENAI_API_KEY.", message)
        self.assertNotIn("DEEPSEEK_API_KEY", message)
        self.assertNotIn("GH_TOKEN", message)
        allowed = json.loads(command[command.index("--settings") + 1])["sandbox"]["network"]["allowedDomains"]
        self.assertIn("api.deepseek.com", allowed)
        self.assertIn("github.com", allowed)

    def test_an_agent_starts_only_the_mcp_servers_in_mains_mcp_json(self):
        path = self.folder / "main" / ".mcp.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"mcpServers": {"blender": {"command": "uvx", "args": ["blender-mcp"]}}}))
        with mock.patch.object(launching, "MCP_JSON", path):
            _, _, command = self.start(resumes=False)
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual(list(json.loads(command[command.index("--mcp-config") + 1])["mcpServers"]), ["blender"])

    def test_without_an_mcp_json_an_agent_starts_no_mcp_server(self):
        with mock.patch.object(launching, "MCP_JSON", self.folder / "main" / ".mcp.json"):
            _, _, command = self.start(resumes=False)
        self.assertIn("--strict-mcp-config", command)
        self.assertNotIn("--mcp-config", command)

    def test_a_resumed_agent_isnt_told_again(self):
        environment, message, _ = self.start(resumes=True)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertEqual(message, "Build issue #1.")


class McpServersTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / ".mcp.json"
        patch = mock.patch.object(launching, "MCP_JSON", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def servers(self, servers: dict) -> dict:
        """The MCP servers an agent gets when main's .mcp.json has these."""
        self.path.write_text(json.dumps({"mcpServers": servers}))
        argument = launching.mcp_arguments()
        self.assertEqual(argument[0], "--mcp-config")
        return json.loads(argument[1])["mcpServers"]

    def run_as_an_agent(self, command: list[str] | str) -> list[str]:
        """The lines a command prints when it runs in an agent's worktree, with the agents' uv cache."""
        environment = {**os.environ, "UV_CACHE_DIR": str(self.folder / "agent-uv-cache")}
        output = subprocess.run(
            command,
            cwd=self.folder,
            env=environment,
            shell=isinstance(command, str),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return output.splitlines()

    def test_a_local_server_runs_in_the_main_checkout_without_the_agents_uv_cache(self):
        server = self.servers({"local": {"command": "/bin/sh", "args": ["-c", "pwd; echo ${UV_CACHE_DIR-unset}"]}})
        lines = self.run_as_an_agent([server["local"]["command"], *server["local"]["args"]])
        self.assertEqual(lines, [os.path.realpath(launching.ROOT), "unset"])

    def test_a_remote_servers_headers_helper_runs_in_the_main_checkout_without_the_agents_uv_cache(self):
        helper = 'pwd; echo ${UV_CACHE_DIR-unset}; echo \'{"X-Token": "a b"}\''
        server = self.servers(
            {"remote": {"type": "http", "url": "https://mcp.example.com/mcp", "headersHelper": helper}}
        )
        lines = self.run_as_an_agent(server["remote"]["headersHelper"])
        self.assertEqual(lines, [os.path.realpath(launching.ROOT), "unset", '{"X-Token": "a b"}'])

    def test_a_remote_server_without_a_headers_helper_is_passed_as_it_is(self):
        remote = {"type": "http", "url": "https://mcp.example.com/mcp"}
        self.assertEqual(self.servers({"remote": remote}), {"remote": remote})

    def test_without_an_mcp_json_there_is_no_server(self):
        self.assertEqual(launching.mcp_arguments(), [])

    def test_launches_are_refused_while_the_mcp_json_isnt_json(self):
        self.assertEqual(launching.mcp_json_refusal(), "")
        self.path.write_text("{")
        self.assertIn("No agent can start", launching.mcp_json_refusal())


class PathRefusalTest(unittest.TestCase):
    def refusal(self, home: Path, main: Path, account_folder: Path | None = None) -> str:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        accounts = Path(folder.name) / "accounts.json"
        account = {"name": "claude-2", "provider": "claude"} | ({"home": str(account_folder)} if account_folder else {})
        accounts.write_text(json.dumps([account]))
        with (
            mock.patch.object(launching, "ROOT", main),
            mock.patch.object(Path, "home", lambda: home),
            mock.patch.object(launching.usage, "ACCOUNTS", accounts),
        ):
            return launching.path_refusal()

    def test_a_home_folder_with_a_pattern_character_is_refused(self):
        for character in "[*?\\":
            home = Path(f"/Volumes/some{character}one")
            with self.subTest(character=character):
                refusal = self.refusal(home, home / "dev" / "app")
                self.assertIn(f"{home} contains", refusal)
                self.assertIn("rename or move it", refusal)

    def test_a_main_checkout_with_a_pattern_character_is_refused(self):
        home = Path("/Volumes/someone")
        for character in "[*?\\":
            main = home / "dev" / f"app{character}old"
            with self.subTest(character=character):
                self.assertIn(f"{main} contains", self.refusal(home, main))

    def test_an_account_folder_with_a_pattern_character_is_refused(self):
        home = Path("/Volumes/someone")
        for character in "[*?\\":
            account_folder = Path(f"/Volumes/accounts/claude{character}2")
            with self.subTest(character=character):
                refusal = self.refusal(home, home / "dev" / "app", account_folder)
                self.assertIn(f"{account_folder} contains", refusal)

    def test_plain_paths_are_accepted(self):
        home = Path("/Volumes/some-one")
        self.assertEqual(self.refusal(home, home / "dev" / "my app", Path("/Volumes/accounts/claude-2")), "")


class GithubLoginTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        gh = self.folder / "bin" / "gh"
        gh.parent.mkdir()
        # Stands in for gh, which would read the maintainer's real login.
        gh.write_text(
            '#!/bin/sh\ncase "$*" in\n'
            '"auth token") echo the-token ;;\n'
            '"auth git-credential get") printf "username=x-access-token\\npassword=from-gh\\n" ;;\n'
            "esac\n"
        )
        gh.chmod(0o755)
        self.planted = self.folder / "planted-helper-ran"
        config = self.folder / "gitconfig"
        # Git adds the action, such as "get", as an argument, which the function ignores.
        config.write_text(f'[credential]\n\thelper = "!f() {{ touch {self.planted}; }}; f"\n')
        environment = {"PATH": f"{gh.parent}:{os.environ['PATH']}", "GIT_CONFIG_GLOBAL": str(config)}
        patched = mock.patch.dict(os.environ, environment | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
        patched.start()
        self.addCleanup(patched.stop)

    def test_gh_gets_the_maintainers_token(self):
        self.assertEqual(launching.github_login()["GH_TOKEN"], "the-token")

    def test_git_asks_only_gh_for_a_login(self):
        credential = subprocess.run(
            ["git", "credential", "fill"],
            cwd=self.folder,
            input="protocol=https\nhost=github.com\n\n",
            env=os.environ | launching.github_login(),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("password=from-gh", credential.stdout)
        self.assertFalse(self.planted.exists())


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def plant_hook(path: Path, marker: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\ntouch {marker}\n")
    path.chmod(0o755)


class NestedRepositoryTest(unittest.TestCase):
    """A builder's worktree, in which the agent made a nested repository whose config runs a program that leaves a
    marker, as an agent can from inside the sandbox."""

    @classmethod
    def setUpClass(cls):
        # Building these repositories with git takes 0.16 s of CPU, so they're built once, not for each test. Git
        # writes absolute paths into them, so setUp copies them to the path where they were built.
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name) / "root"
        cls.built_folder = Path(folder.name) / "built"
        root = cls.root
        root.mkdir()
        run_git(root, "init", "--quiet", "--bare", "origin.git")
        run_git(root, "clone", "--quiet", str(root / "origin.git"), "main")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com"), ("commit.gpgsign", "false")):
            run_git(root / "main", "config", key, value)
        run_git(root / "main", "commit", "--quiet", "--allow-empty", "-m", "start")
        run_git(root / "main", "push", "--quiet", "origin", "HEAD:main")
        run_git(root / "main", "worktree", "add", "--quiet", "-b", "issue-1", str(root / "worktree"))
        nested = root / "worktree" / "vendor" / "repo"
        nested.mkdir(parents=True)
        run_git(root, "init", "--quiet", "--separate-git-dir", str(root / "planted"), str(nested))
        run_git(nested, "config", "filter.evil.clean", f"touch {root / 'marker'}; cat")
        (nested / ".gitattributes").write_text("* filter=evil\n")
        (nested / "file").write_text("planted\n")
        (root / "worktree" / "new.py").write_text("print('work')\n")
        root.rename(cls.built_folder)

    def setUp(self):
        shutil.copytree(self.built_folder, self.root, symlinks=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.main = self.root / "main"
        self.worktree = self.root / "worktree"
        self.marker = self.root / "marker"
        self.nested = self.worktree / "vendor" / "repo"

    def committed(self) -> list[str]:
        return run_git(self.worktree, "ls-tree", "-r", "--name-only", "origin/issue-1").split()

    def test_an_untracked_nested_repository_is_left_out_and_the_rest_saved(self):
        self.assertEqual(launching.save_work(self.worktree), "")
        self.assertEqual(self.committed(), ["new.py"])
        self.assertFalse(self.marker.exists())

    def stage_nested(self) -> None:
        """Commits the nested repository into the worktree as a gitlink, then changes it."""
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com"), ("commit.gpgsign", "false")):
            run_git(self.nested, "config", key, value)
        run_git(self.nested, "add", "--all")
        run_git(self.nested, "commit", "--quiet", "-m", "start")
        run_git(self.worktree, "add", "-f", "vendor/repo")
        run_git(self.worktree, "commit", "--quiet", "-m", "add the repository")
        self.marker.unlink(missing_ok=True)
        (self.nested / "file").write_text("changed\n")

    def test_a_nested_repository_the_agent_staged_runs_nothing_when_the_work_is_saved_and_removed(self):
        self.stage_nested()
        self.assertEqual(launching.save_work(self.worktree), "")
        self.assertIn("new.py", self.committed())
        with mock.patch.dict(launching.git.__kwdefaults__, {"cwd": self.main}):
            launching.remove_worktree(self.worktree)
        self.assertFalse(self.worktree.exists())
        self.assertFalse(self.marker.exists())

    def test_a_nested_repository_the_agent_staged_runs_nothing_when_git_reads_the_worktree(self):
        self.stage_nested()
        run_git(self.nested, "config", "core.fsmonitor", f"touch {self.marker}; false")
        launching.worktree_git(self.worktree, "status", "--porcelain")
        self.assertFalse(self.marker.exists())

    def test_a_nested_repository_that_gitmodules_marks_ignore_none_runs_no_fsmonitor(self):
        self.stage_nested()
        (self.worktree / ".gitmodules").write_text('[submodule "repo"]\n\tpath = vendor/repo\n\tignore = none\n')
        # No setting stops a nested repository's filter, so this test removes it and checks only the settings.
        run_git(self.nested, "config", "--unset", "filter.evil.clean")
        run_git(self.nested, "config", "core.fsmonitor", f"touch {self.marker}; false")
        launching.worktree_git(self.worktree, "status", "--porcelain")
        self.assertFalse(self.marker.exists())

    def plant_main_hooks(self, *events: str) -> None:
        """Hooks in the main checkout's hooks folder and in its git config, for the events."""
        plant_hook(self.root / "hook", self.marker)
        run_git(self.main, "config", "hook.tests.command", str(self.root / "hook"))
        for event in events:
            plant_hook(self.main / ".git" / "hooks" / event, self.marker)
            run_git(self.main, "config", "--add", "hook.tests.event", event)

    def test_the_main_checkouts_hooks_dont_run_when_the_work_is_saved_or_a_successor_checks_it_out(self):
        # The five events whose hooks a save ran, when hooks were set for every event, and a successor's checkout.
        self.plant_main_hooks(
            "prepare-commit-msg", "post-commit", "post-index-change", "reference-transaction", "pre-push", "post-checkout"
        )
        self.assertEqual(launching.save_work(self.worktree), "")
        self.assertIn("new.py", self.committed())
        self.assertFalse(self.marker.exists())
        successor = self.root / "successor"
        with mock.patch.dict(launching.git.__kwdefaults__, {"cwd": self.main}):
            launching.remove_worktree(self.worktree)
            launching.add_branch_worktree(successor, 1, continues=True)
        self.assertTrue((successor / "new.py").exists())
        self.assertFalse(self.marker.exists())

    def test_a_worktree_with_only_a_nested_repository_has_no_changes(self):
        (self.worktree / "new.py").unlink()
        self.assertEqual(launching.changes(self.worktree, launching.nested_repositories(self.worktree)), "")

    def test_the_report_names_what_was_left_out(self):
        with mock.patch.object(launching.github, "gh_json", return_value=[]):
            report = launching.saved_work(self.worktree)
        self.assertIn("vendor/repo is a nested git repository, so the loop didn't save it.", report)


class HoldingTest(unittest.TestCase):
    PORCELAIN = (
        "worktree /repo\nHEAD 1\nbranch refs/heads/main\n\n"
        "worktree /repo/.loop/worktrees/builder-42-a\nHEAD 2\nbranch refs/heads/issue-42\n\n"
        "worktree /repo/.loop/worktrees/builder-4-b\nHEAD 3\nbranch refs/heads/issue-4\n\n"
        "worktree /repo/.loop/worktrees/reviewer-9-c\nHEAD 4\ndetached"
    )

    def test_only_the_worktree_with_exactly_that_branch(self):
        self.assertEqual(launching.holding(self.PORCELAIN, "issue-4"), [Path("/repo/.loop/worktrees/builder-4-b")])

    def test_a_branch_nobody_holds(self):
        self.assertEqual(launching.holding(self.PORCELAIN, "issue-5"), [])


class BuiltTest(unittest.TestCase):
    def test_the_record_says_whether_the_agent_builds(self):
        self.assertFalse(launching.built({"name": "reviewer", "builds": False}))

    def test_a_record_without_the_answer_counts_as_a_builders(self):
        self.assertTrue(launching.built({"name": "builder-old"}))


class BusyWorktreeTest(unittest.TestCase):
    def test_a_stopped_agents_worktree_can_be_freed(self):
        self.assertEqual(launching.busy_worktree(launching.WORKTREES / "builder-7-abc", "issue-7", running=[]), "")

    def test_a_running_agents_worktree_is_refused(self):
        path = launching.WORKTREES / "builder-7-abc"
        reason = launching.busy_worktree(path, "issue-7", running=[{"worktree": str(path)}])
        self.assertIn("still works in", reason)

    def test_a_worktree_outside_the_loops_folder_is_refused(self):
        reason = launching.busy_worktree(Path("/work/my-checkout"), "issue-7", running=[])
        self.assertIn("isn't one of the loop's worktrees", reason)


class PushAdviceTest(unittest.TestCase):
    REJECTED = (
        "To github.com:owner/repo.git\n ! [rejected]        issue-4 -> issue-4 (non-fast-forward)\n"
        "error: failed to push some refs to 'github.com:owner/repo.git'\n"
        "hint: Updates were rejected because the tip of your current branch is behind\n"
        "hint: its remote counterpart. If you want to integrate the remote changes,\n"
        "hint: use 'git pull' before pushing again.\n"
    )
    OFFLINE = "fatal: unable to access 'https://github.com/owner/repo.git/': Could not resolve host: github.com\n"

    def test_git_hints_are_left_out(self):
        error = launching.push_error(self.REJECTED)
        self.assertIn("[rejected]", error)
        self.assertNotIn("git pull", error)

    def test_a_refused_push_goes_to_the_maintainer(self):
        advice = launching.push_advice(launching.push_error(self.REJECTED), Path("/w"))
        self.assertIn("Tell the maintainer", advice)

    def test_a_push_a_ruleset_declines_goes_to_the_maintainer(self):
        declined = (
            "remote: error: GH013: Repository rule violations found for refs/heads/issue-4.\n"
            " ! [remote rejected] issue-4 -> issue-4 (push declined due to repository rule violations)\n"
        )
        self.assertIn("Tell the maintainer", launching.push_advice(launching.push_error(declined), Path("/w")))

    def test_a_network_failure_is_pushed_again(self):
        advice = launching.push_advice(launching.push_error(self.OFFLINE), Path("/w"))
        self.assertIn("pushes it again", advice)
        self.assertNotIn("maintainer", advice)

    def test_the_cause_of_an_ssh_failure_is_kept(self):
        offline = (
            "ssh: Could not resolve hostname github.com: nodename nor servname provided\n"
            "fatal: Could not read from remote repository.\n\n"
            "Please make sure you have the correct access rights\nand the repository exists.\n"
        )
        error = launching.push_error(offline)
        self.assertIn("Could not resolve hostname", error)
        self.assertNotIn("Please make sure", error)


class HandleTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.note = Path(folder.name) / "issue-7.md"
        self.note.write_text("Left: the tests.")
        self.session = launching.Session("builder-7-abc", "id", "claude", Path(folder.name), mock.MagicMock(), Path())
        self.session.note = self.note

    def test_the_note_is_kept_until_the_agent_answers(self):
        launching.handle(self.session, b'{"type": "system", "subtype": "init"}')
        self.assertTrue(self.note.exists())
        launching.handle(self.session, b'{"type": "assistant", "message": {"model": "claude-haiku-5-5"}}')
        self.assertFalse(self.note.exists())

    def test_the_note_is_kept_when_the_first_request_fails(self):
        launching.handle(self.session, b'{"type": "assistant", "error": "rate_limit"}')
        launching.handle(self.session, b'{"type": "assistant", "message": {"model": "<synthetic>"}}')
        self.assertTrue(self.note.exists())

    def test_a_result_saves_each_models_context_window(self):
        with mock.patch.object(launching.usage, "CONTEXT_WINDOWS", self.note.parent / "context-windows.json"):
            result = {"type": "result", "result": "Done.", "modelUsage": {"claude-x": {"contextWindow": 200_000}}}
            launching.handle(self.session, json.dumps(result).encode())
            self.assertEqual(launching.usage.context_window("claude", "claude-x"), 200_000)

    def test_a_note_written_later_is_kept(self):
        launching.handle(self.session, b'{"type": "assistant"}')
        self.note.write_text("Left: the docs.")
        launching.handle(self.session, b'{"type": "assistant"}')
        self.assertTrue(self.note.exists())


class IsDoneTest(unittest.TestCase):
    """An agent that has reported."""

    def setUp(self):
        self.session = launching.Session("builder-7-abc", "id", "claude", Path(), mock.MagicMock(), Path())
        launching.handle(self.session, b'{"type": "result", "result": "Done."}')

    def go_quiet(self):
        self.session.last_output = time.time() - launching.QUIET_SECONDS - 1

    def test_an_agent_with_nothing_in_the_background_is_done_once_quiet(self):
        self.assertFalse(launching.is_done(self.session))
        self.go_quiet()
        self.assertTrue(launching.is_done(self.session))

    def test_an_agent_is_done_only_once_its_background_tasks_end(self):
        tasks = {"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "b1"}]}
        launching.handle(self.session, json.dumps(tasks).encode())
        self.go_quiet()
        self.assertFalse(launching.is_done(self.session))
        launching.handle(self.session, json.dumps({**tasks, "tasks": []}).encode())
        self.go_quiet()
        self.assertTrue(launching.is_done(self.session))

    def test_claude_codes_own_ambient_tasks_dont_hold_an_agent(self):
        tasks = {"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "w1", "ambient": True}]}
        launching.handle(self.session, json.dumps(tasks).encode())
        self.go_quiet()
        self.assertTrue(launching.is_done(self.session))


class CollectNoteTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.worktree = self.root / "builder-7-abc"
        (self.worktree / ".loop").mkdir(parents=True)
        self.secret = self.root / "secret"
        self.secret.write_text("a password")
        patch = mock.patch.object(launching.usage, "HANDOFF", self.root / "handoff")
        patch.start()
        self.addCleanup(patch.stop)

    def collected(self) -> str | None:
        launching.collect_note(self.worktree, 7)
        note = self.root / "handoff" / "issue-7.md"
        return note.read_text() if note.exists() else None

    def test_a_note_moves_under_the_issue(self):
        (self.worktree / ".loop" / "handoff.md").write_text("Left: the tests.")
        self.assertEqual(self.collected(), "Left: the tests.")
        self.assertFalse((self.worktree / ".loop" / "handoff.md").exists())

    def test_no_note(self):
        self.assertIsNone(self.collected())

    def test_a_link_to_another_file_is_ignored(self):
        (self.worktree / ".loop" / "handoff.md").symlink_to(self.secret)
        self.assertIsNone(self.collected())

    def test_a_hard_link_to_another_file_is_ignored(self):
        os.link(self.secret, self.worktree / ".loop" / "handoff.md")
        self.assertIsNone(self.collected())

    def test_a_named_pipe_is_ignored(self):
        os.mkfifo(self.worktree / ".loop" / "handoff.md")
        self.assertIsNone(self.collected())

    def test_a_folder_is_ignored(self):
        (self.worktree / ".loop" / "handoff.md").mkdir()
        self.assertIsNone(self.collected())

    def test_text_that_isnt_utf_8_is_kept_readable(self):
        (self.worktree / ".loop" / "handoff.md").write_bytes(b"\xff\xfeLeft: the tests.")
        self.assertTrue((self.collected() or "").endswith("Left: the tests."))

    def test_a_folder_swapped_for_a_link_while_the_note_is_read_keeps_the_other_file(self):
        (self.worktree / ".loop" / "handoff.md").write_text("Left: the tests.")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "handoff.md").write_text("a password")
        read = launching.plain_file_text

        def read_then_swap(folder: int, name: str) -> str | None:
            text = read(folder, name)
            (self.worktree / ".loop").rename(self.root / "moved")
            (self.worktree / ".loop").symlink_to(elsewhere)
            return text

        # The swap must land between the read and the delete, and only the project's own code runs between them.
        with mock.patch.object(launching, "plain_file_text", read_then_swap):
            self.assertEqual(self.collected(), "Left: the tests.")
        self.assertTrue((elsewhere / "handoff.md").exists())

    def test_a_linked_folder_is_ignored(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "handoff.md").write_text("a password")
        (self.worktree / ".loop").rmdir()
        (self.worktree / ".loop").symlink_to(elsewhere)
        self.assertIsNone(self.collected())


class NoteForTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(launching.usage, "HANDOFF", Path(folder.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.note = Path(folder.name) / "issue-7.md"
        self.note.write_text("Left: the tests.\nRun them with make test.")

    def test_a_builder_that_continues_gets_the_note_quoted_and_the_commits(self):
        note = launching.note_for(7, continues=True)
        self.assertEqual(note, self.note)
        message = launching.successor_message(7, note, "abc1234 feat: add the parser")
        self.assertIn("\n\n> Left: the tests.\n> Run them with make test.\n\n", message)
        self.assertTrue(message.endswith("\n\nabc1234 feat: add the parser"))

    def test_a_builder_that_starts_from_main_deletes_the_note(self):
        self.assertIsNone(launching.note_for(7, continues=False))
        self.assertFalse(self.note.exists())

    def test_a_successor_without_a_note_or_commits_is_told_so(self):
        message = launching.successor_message(7, None, "")
        self.assertIn("without a hand-off note", message)
        self.assertIn("no commits yet", message)


if __name__ == "__main__":
    unittest.main()
