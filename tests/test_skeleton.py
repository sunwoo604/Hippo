import subprocess
import sys
from pathlib import Path

import pytest

from hippo import paths
from hippo.cli import main
from tests.conftest import git

COMMANDS = ["install", "link", "unlink", "sync", "status", "open"]


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for cmd in COMMANDS:
        assert cmd in out


def test_runs_as_module():
    r = subprocess.run(
        [sys.executable, "-m", "hippo", "--version"], capture_output=True, text=True
    )
    assert r.returncode == 0
    assert r.stdout.startswith("hippo ")


def test_paths_follow_home(home):
    assert paths.root() == home / "agent-memory"
    assert paths.launcher() == home / "agent-memory" / "bin" / "hippo"
    assert paths.local_bin_link() == home / ".local" / "bin" / "hippo"
    assert paths.project_dir("abc") == home / "agent-memory" / "projects" / "abc"


def test_fake_claude_records_calls(fake_claude, tmp_path):
    r = subprocess.run(
        ["claude", "-p", "--output-format", "json"],
        input="hello", capture_output=True, text=True, cwd=tmp_path,
    )
    assert r.returncode == 0
    [call] = fake_claude.calls()
    assert call["argv"] == ["-p", "--output-format", "json"]
    assert call["stdin"] == "hello"
    assert Path(call["cwd"]) == tmp_path


def test_git_repo_fixture(git_repo):
    assert git(git_repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
