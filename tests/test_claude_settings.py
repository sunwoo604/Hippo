import json
import shutil
from pathlib import Path

import pytest

from hippo.cli import main

REPO = Path(__file__).parent.parent


@pytest.fixture
def installed(fake_claude, capsys):
    assert main(["install"]) == 0
    capsys.readouterr()


@pytest.fixture
def local(git_repo):
    return git_repo / ".claude" / "settings.local.json"


def link(repo, monkeypatch):
    monkeypatch.chdir(repo)
    return main(["link", "--no-init"])


def hippo_commands(data, event):
    return [h["command"] for g in data.get("hooks", {}).get(event, []) for h in g["hooks"]
            if "_hook" in h["command"]]


def test_link_writes_repo_hooks_and_rule(installed, git_repo, monkeypatch, local, home, capsys):
    assert link(git_repo, monkeypatch) == 0
    data = json.loads(local.read_text())
    launcher = home / "agent-memory" / "bin" / "hippo"
    for event, name in (("SessionStart", "session-start"), ("Stop", "stop"),
                        ("SessionEnd", "session-end")):
        assert hippo_commands(data, event) == [f"{launcher} _hook {name}"]
    assert data["permissions"]["allow"] == [f"Bash({launcher} patch new *)"]
    assert ".claude/settings.local.json" in (git_repo / ".gitignore").read_text().splitlines()
    out = capsys.readouterr().out
    assert "created   .claude/settings.local.json: hooks (session start, stop, session end)" in out


def test_global_settings_never_touched(installed, git_repo, monkeypatch, home):
    link(git_repo, monkeypatch)
    main(["install"])
    assert not (home / ".claude" / "settings.json").exists()


def test_keeps_everything_else(installed, git_repo, monkeypatch, local):
    local.parent.mkdir()
    mine = {
        "permissions": {"allow": ["Bash(npm test)"], "deny": ["Read(./.env)"]},
        "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "say done"}]}]},
        "enabledMcpjsonServers": ["x"],
    }
    local.write_text(json.dumps(mine))
    link(git_repo, monkeypatch)
    data = json.loads(local.read_text())
    assert data["enabledMcpjsonServers"] == ["x"]
    assert data["permissions"]["deny"] == ["Read(./.env)"]
    assert data["permissions"]["allow"][0] == "Bash(npm test)"
    assert data["hooks"]["Stop"][0] == mine["hooks"]["Stop"][0]
    assert len(data["hooks"]["Stop"]) == 2


def test_backup_kept_when_file_existed(installed, git_repo, monkeypatch, local, home):
    local.parent.mkdir()
    local.write_text('{"x": 1}\n')
    link(git_repo, monkeypatch)
    [backup] = (home / "agent-memory" / "backups").glob(f"{git_repo.name}-settings.local-*.json")
    assert backup.read_text() == '{"x": 1}\n'


def test_invalid_json_left_untouched_link_still_works(installed, git_repo, monkeypatch, local, capsys):
    local.parent.mkdir()
    local.write_text("{oops")
    assert link(git_repo, monkeypatch) == 0
    assert local.read_text() == "{oops"
    assert (git_repo / ".hippo").exists()
    assert "skipped   .claude/settings.local.json: not valid JSON" in capsys.readouterr().out


def test_install_refreshes_linked_repos(installed, git_repo, monkeypatch, local, capsys):
    link(git_repo, monkeypatch)
    # Simulate an old launcher path left behind in the repo's settings.
    data = json.loads(local.read_text())
    data["hooks"]["Stop"][0]["hooks"][0]["command"] = "/old/hippo _hook stop"
    data["permissions"]["allow"] = ["Bash(/old/hippo patch new *)"]
    local.write_text(json.dumps(data))
    capsys.readouterr()
    main(["install"])
    fixed = json.loads(local.read_text())
    assert "/old/" not in json.dumps(fixed)
    assert len(hippo_commands(fixed, "Stop")) == 1
    assert "settings.local.json: hooks" in capsys.readouterr().out


def test_install_refresh_is_quiet_when_current(installed, git_repo, monkeypatch, local, capsys):
    link(git_repo, monkeypatch)
    before = local.read_text()
    capsys.readouterr()
    main(["install"])
    assert local.read_text() == before
    assert "ok        " in capsys.readouterr().out


def test_install_skips_missing_or_unlinked_repos(installed, git_repo, monkeypatch, capsys):
    link(git_repo, monkeypatch)
    (git_repo / ".hippo").unlink()
    shutil.rmtree(git_repo / ".claude")
    assert main(["install"]) == 0
    assert not (git_repo / ".claude").exists()


def test_plugin_ships_no_global_hooks():
    assert not (REPO / "hooks").exists()


def test_skill_has_frontmatter():
    text = (REPO / "skills" / "hippo" / "SKILL.md").read_text()
    assert text.startswith("---\nname: hippo\ndescription: ")
    assert "Never run `hippo link` yourself" in text
