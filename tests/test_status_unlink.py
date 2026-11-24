import json
import shutil
from datetime import datetime

import pytest

from hippo import deps, paths, registry, schedule
from hippo.cli import main
from tests.conftest import git
from tests.test_sync import agent_does, mem, note  # noqa: F401

TODAY = f"{datetime.now():%Y-%m-%d}"


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")


@pytest.fixture
def answer(monkeypatch):
    """Pretend to be a terminal and type `text` at the confirmation."""
    def type_(text):
        monkeypatch.setattr(deps, "is_interactive", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt: print(prompt) or text)
    return type_


def uuid_of(mem):
    return mem.name


def archived(home):
    return list((home / "agent-memory/archived-projects").glob("*"))


# --- hippo unlink -----------------------------------------------------------------------

def test_unlink_archives_memory_and_removes_hooks(mem, git_repo, home, answer, capsys):
    uuid = uuid_of(mem)
    (home / "agent-memory/state" / uuid).mkdir(parents=True)
    (home / "agent-memory/state" / uuid / "s1.json").write_text("{}")
    note(mem)
    answer("y")
    assert main(["unlink"]) == 0
    out = capsys.readouterr().out
    assert "1 patch note(s) not synced yet" in out and "Continue? [y/N]" in out

    assert not (git_repo / ".hippo").exists()
    assert not (git_repo / ".claude/settings.local.json").exists()  # held only Hippo's entries
    assert registry.load() == []
    assert not mem.exists() and not (home / "agent-memory/state" / uuid).exists()
    [dest] = archived(home)
    assert dest.name == f"repo-{TODAY}-{uuid[:8]}"
    assert (dest / "patches/2026-10-20T101010.md").exists()
    assert json.loads((dest / "meta.json").read_text())["unlinked_at"].startswith(TODAY)
    assert "hippo link" in out and ".gitignore" in out
    assert f"unlinked; memory archived" in (home / f"agent-memory/logs/{TODAY}.log").read_text()

    # Linking again starts fresh: the archive isn't offered for reuse.
    assert main(["link", "--no-init"]) == 0
    assert json.loads((git_repo / ".hippo").read_text())["uuid"] != uuid


def test_unlink_keeps_the_users_own_settings(mem, git_repo, answer):
    f = git_repo / ".claude/settings.local.json"
    settings = json.loads(f.read_text())
    settings["permissions"]["allow"].append("Bash(ls)")
    settings["model"] = "opus"
    f.write_text(json.dumps(settings))
    answer("yes")
    assert main(["unlink"]) == 0
    assert json.loads(f.read_text()) == {"permissions": {"allow": ["Bash(ls)"]}, "model": "opus"}


@pytest.mark.parametrize("typed", ["", "n"])
def test_unlink_defaults_to_no(mem, git_repo, answer, capsys, typed):
    answer(typed)
    assert main(["unlink"]) == 1
    assert "nothing changed" in capsys.readouterr().out
    assert (git_repo / ".hippo").exists() and mem.exists() and registry.load()


def test_unlink_outside_a_terminal_needs_yes(mem, git_repo, capsys, home):
    assert main(["unlink"]) == 1
    err = capsys.readouterr().err
    assert "needs a confirmation" in err and f"hippo unlink {uuid_of(mem)} --yes" in err
    assert (git_repo / ".hippo").exists() and mem.exists()
    assert main(["unlink", "--yes"]) == 0
    assert not mem.exists() and len(archived(home)) == 1


def test_unlink_a_deleted_repo_from_anywhere(mem, git_repo, tmp_path, monkeypatch, home):
    shutil.rmtree(git_repo)
    monkeypatch.chdir(tmp_path)
    assert main(["unlink", "repo", "--yes"]) == 0
    assert registry.load() == [] and not mem.exists() and len(archived(home)) == 1


def test_unlink_by_uuid_prefix_leaves_a_relinked_repo_alone(mem, git_repo, tmp_path, monkeypatch):
    """The repo now points at other memory: archive the old one, don't touch the repo."""
    old = uuid_of(mem)
    (git_repo / ".hippo").write_text(json.dumps({"uuid": "other"}))
    monkeypatch.chdir(tmp_path)
    assert main(["unlink", old[:8], "--yes"]) == 0
    assert json.loads((git_repo / ".hippo").read_text()) == {"uuid": "other"}
    assert (git_repo / ".claude/settings.local.json").exists()
    assert not mem.exists()


def test_unlink_ambiguous_or_unknown_name(mem, git_repo, tmp_path, monkeypatch, capsys):
    registry.save(registry.load() + [{"uuid": "abcd-2", "repo_path": str(tmp_path / "x/repo")}])
    monkeypatch.chdir(tmp_path)
    assert main(["unlink", "repo", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "more than one linked repo matches 'repo'" in err and "hippo unlink abcd-2" in err
    assert main(["unlink", "nope", "--yes"]) == 1
    assert "no linked repo called 'nope'" in capsys.readouterr().err
    assert mem.exists()


def test_unlink_refused_for_agents_and_unlinked_repos(mem, git_repo, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_AGENT", "1")
    assert main(["unlink", "--yes"]) == 1
    monkeypatch.delenv("HIPPO_AGENT")
    (git_repo / ".hippo").unlink()
    assert main(["unlink", "--yes"]) == 1
    assert "isn't linked to Hippo" in capsys.readouterr().err
    assert mem.exists()


def test_unlink_waits_for_a_running_sync(mem, git_repo, capsys):
    import os
    paths.lock_file().write_text(str(os.getpid()))
    assert main(["unlink", "--yes"]) == 1
    assert "already running" in capsys.readouterr().err
    assert (git_repo / ".hippo").exists() and mem.exists()


# --- hippo status -----------------------------------------------------------------------

def status(capsys):
    capsys.readouterr()
    assert main(["status"]) == 0
    return capsys.readouterr().out


def test_status_shows_each_repo_and_the_daily_job(mem, git_repo, mac, agent_does, capsys):
    note(mem)
    (mem / "needs-review.json").write_text(json.dumps([
        {"id": "r-001", "status": "open"}, {"id": "r-002", "status": "dismissed"}]))
    git(git_repo, "commit", "-q", "--allow-empty", "-m", "one")
    git(git_repo, "commit", "-q", "--allow-empty", "-m", "two")
    out = status(capsys)
    lines = out.splitlines()
    assert lines[0].split() == ["repo", "last", "sync", "unsynced", "notes", "needs", "review",
                                "new", "commits"]
    assert lines[1].split() == ["repo", "never", "1", "1", "2"]
    assert "daily sync  03:00, loaded · hasn't run yet" in out
    assert "claude      logged in" in out

    agent_does()
    assert main(["sync"]) == 0
    row = status(capsys).splitlines()[1].split()
    assert row[1] == TODAY and row[3:] == ["0", "1", "0"]


def test_status_reports_problems_with_the_fix(mem, git_repo, mac, monkeypatch, capsys):
    shutil.rmtree(git_repo)
    monkeypatch.setenv("FAKE_LAUNCHCTL_EXIT", "113")
    monkeypatch.setenv("FAKE_CLAUDE_LOGGED_IN", "0")
    out = status(capsys)
    assert f"! repo: repo not found at" in out and f"hippo unlink {uuid_of(mem)[:8]}" in out
    assert "03:00, NOT loaded: run `hippo install`" in out
    assert "logged out: run `claude auth login`" in out


def test_status_shows_the_last_scheduled_failure(mem, mac, capsys):
    schedule.launchd_log().write_text("--- 2026-11-23 03:07 daily sync\nok        repo: ...\n")
    assert "last run 2026-11-23 03:07" in status(capsys)  # from before outcomes were recorded
    schedule.save_outcome(False, "failed for repo")
    assert "last run " in status(capsys) and ": failed for repo" in status(capsys)


def test_status_with_nothing_linked(fake_claude, mac, capsys):
    assert main(["install"]) == 0
    out = status(capsys)
    assert "No repos linked yet." in out and "hippo link" in out
    shutil.rmtree(paths.root())
    assert main(["status"]) == 1
