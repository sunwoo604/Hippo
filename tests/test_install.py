import json
import os
import shutil
import subprocess
import sys

import pytest

from hippo import deps
from hippo.cli import main
from hippo.ui import NEXT_STEPS_HEADING


@pytest.fixture
def claude_md(home):
    return home / ".claude" / "CLAUDE.md"


@pytest.fixture
def root(home):
    return home / "agent-memory"


def install(*args):
    return main(["install", *args])


def test_fresh_install(fake_claude, root, home, capsys):
    assert install() == 0

    for d in ("bin", "logs", "projects"):
        assert (root / d).is_dir()
    assert not (root / "prompts").exists()  # prompts ship with the package
    assert json.loads((root / "projects.json").read_text()) == []
    assert json.loads((root / "config.json").read_text())["route"] == "cli"

    out = capsys.readouterr().out
    assert NEXT_STEPS_HEADING in out
    assert "link" in out.split(NEXT_STEPS_HEADING)[1]


def test_never_touches_global_claude_md(fake_claude, claude_md):
    claude_md.parent.mkdir()
    claude_md.write_text("# My rules\n")
    install()
    assert claude_md.read_text() == "# My rules\n"


def test_no_claude_md_created(fake_claude, claude_md):
    install()
    assert not claude_md.exists()


def test_reinstall_is_idempotent(fake_claude, root, capsys):
    install()
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    capsys.readouterr()

    assert install() == 0

    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
    out = capsys.readouterr().out
    assert "created" not in out and "updated" not in out


def test_projects_json_never_overwritten(fake_claude, root):
    install()
    (root / "projects.json").write_text('[{"uuid": "x", "repo_path": "/r"}]\n')
    install()
    assert json.loads((root / "projects.json").read_text())[0]["uuid"] == "x"


def test_leftover_prompt_copies_noted_not_deleted(fake_claude, root, capsys):
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "init.md").write_text("old copy\n")
    install()
    assert "no longer used" in capsys.readouterr().out
    assert (root / "prompts" / "init.md").read_text() == "old copy\n"


def test_missing_claude_fails_before_writing(tmp_path, monkeypatch, root, capsys):
    only_git = tmp_path / "onlygit"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(only_git))

    assert install() == 1
    assert "claude" in capsys.readouterr().err
    assert not root.exists()


@pytest.fixture
def logged_out(fake_claude, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLAUDE_LOGGED_IN", "0")
    monkeypatch.setenv("FAKE_CLAUDE_LOGIN_FILE", str(tmp_path / "logged-in"))
    return fake_claude


def _terminal(monkeypatch, answer):
    monkeypatch.setattr(deps, "is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)


def _login_calls(fake):
    return [c for c in fake.calls() if c["argv"][:2] == ["auth", "login"]]


def test_logged_out_without_terminal_fails_before_writing(
    logged_out, monkeypatch, root, capsys
):
    monkeypatch.setattr(deps, "is_interactive", lambda: False)
    assert install() == 1
    err = capsys.readouterr().err
    assert NEXT_STEPS_HEADING in err
    assert "claude auth login" in err.split(NEXT_STEPS_HEADING)[1]
    assert not root.exists()
    assert _login_calls(logged_out) == []


@pytest.mark.parametrize("answer", ["", "y", "YES"])
def test_logged_out_in_terminal_offers_login(logged_out, monkeypatch, root, answer):
    _terminal(monkeypatch, answer)
    assert install() == 0
    assert len(_login_calls(logged_out)) == 1
    assert (root / "projects.json").exists()


def test_declining_login_fails_before_writing(logged_out, monkeypatch, root):
    _terminal(monkeypatch, "n")
    assert install() == 1
    assert _login_calls(logged_out) == []
    assert not root.exists()


def test_failed_login_reported(logged_out, monkeypatch, root, capsys):
    monkeypatch.setenv("FAKE_CLAUDE_LOGIN_EXIT", "1")
    _terminal(monkeypatch, "")
    assert install() == 1
    assert "still not logged in" in capsys.readouterr().err
    assert not root.exists()


def test_logged_in_never_prompts(fake_claude, monkeypatch):
    monkeypatch.setattr(deps, "is_interactive", lambda: True)
    def answer(prompt):
        assert "Log in" not in prompt, "asked to log in while logged in"
        return "y"
    monkeypatch.setattr("builtins.input", answer)
    assert install() == 0
    assert _login_calls(fake_claude) == []


def test_existing_foreign_command_link_left_alone(fake_claude, home, capsys):
    link = home / ".local" / "bin" / "hippo"
    link.parent.mkdir(parents=True)
    link.write_text("#!/bin/sh\necho other\n")
    install()
    assert link.read_text() == "#!/bin/sh\necho other\n"
    assert "points elsewhere" in capsys.readouterr().out


def _stub_claude(tmp_path, monkeypatch, body):
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(f"#!/usr/bin/env python3\nimport sys, time, json\n{body}\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_claude_never_waits_on_our_stdin(tmp_path, monkeypatch, root):
    # A claude that reads stdin must not hang when hippo's own stdin stays open.
    _stub_claude(tmp_path, monkeypatch,
                 'sys.stdin.read(); print(json.dumps({"loggedIn": True}))')
    proc = subprocess.Popen(
        [sys.executable, "-m", "hippo", "install"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        out, err = proc.communicate(timeout=20)
    finally:
        proc.kill()
    assert proc.returncode == 0, err
    assert (root / "projects.json").exists()


def test_auth_timeout_reported(tmp_path, monkeypatch, root, capsys):
    _stub_claude(tmp_path, monkeypatch, "time.sleep(10)")
    monkeypatch.setattr(deps, "AUTH_TIMEOUT", 1)
    assert install() == 1
    assert "did not respond" in capsys.readouterr().err
    assert not root.exists()
