import json
import os
import plistlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hippo import paths, schedule, sync
from hippo.cli import main
from tests.conftest import git
from tests.test_sync import agent_calls, agent_does, edit, mem, note, today_log  # noqa: F401


def calls(tool):
    log = Path(os.environ[f"FAKE_{tool.upper()}_LOG"])
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def plist():
    return plistlib.loads(schedule.plist_file().read_bytes())


def config():
    return json.loads(paths.config_file().read_text())


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")


# --- hippo install sets up the job ------------------------------------------------

def test_install_writes_and_loads_the_job(fake_claude, mac, capsys, home):
    assert main(["install"]) == 0
    p = plist()
    assert schedule.plist_file() == home / "Library/LaunchAgents/com.hippo.sync.plist"
    assert p["ProgramArguments"] == [str(paths.launcher()), "sync", "--all", "--scheduled"]
    assert p["StartCalendarInterval"] == {"Hour": 3, "Minute": 0}
    assert p["RunAtLoad"] is True
    assert p["StandardOutPath"] == str(home / "agent-memory/logs/launchd.log")
    claude_dir = str(Path(fake_claude.log).parent / "fakebin")
    env = p["EnvironmentVariables"]
    assert env["PATH"].split(":")[0] == claude_dir
    assert env["HOME"] == str(home) and env["USER"]  # claude needs USER to find its login
    uid = os.getuid()
    assert calls("launchctl") == [["bootout", f"gui/{uid}/com.hippo.sync"],
                                  ["bootstrap", f"gui/{uid}", str(schedule.plist_file())]]
    assert schedule.last_run() is not None  # loading the job doesn't sync everything at once
    out = capsys.readouterr().out
    assert "daily sync at 03:00 (com.hippo.sync.plist): model sonnet, up to 50 turns per agent run" in out
    assert "Not set up yet" not in out

    assert main(["install"]) == 0
    assert "ok        daily sync at 03:00" in capsys.readouterr().out


def test_install_options(fake_claude, mac, capsys):
    assert main(["install"]) == 0
    assert main(["install", "--sync-at", "2:30", "--sync-model", "haiku"]) == 0
    assert plist()["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}
    assert config()["sync_at"] == "02:30" and config()["sync_model"] == "haiku"
    assert "updated   daily sync at 02:30" in capsys.readouterr().out
    assert main(["install"]) == 0  # settings stick
    assert plist()["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}


def test_bad_time_changes_nothing(fake_claude, mac, capsys):
    assert main(["install", "--sync-at", "25:00"]) == 1
    assert "isn't a time like 03:00" in capsys.readouterr().err
    assert not paths.config_file().exists() and not schedule.plist_file().exists()


def test_no_schedule_removes_the_job_until_turned_back_on(fake_claude, mac, capsys):
    assert main(["install"]) == 0
    assert main(["install", "--no-schedule"]) == 0
    assert not schedule.plist_file().exists()
    assert calls("launchctl")[-1] == ["bootout", f"gui/{os.getuid()}/com.hippo.sync"]
    assert "removed   daily sync is off" in capsys.readouterr().out
    assert main(["install"]) == 0
    assert not schedule.plist_file().exists()
    assert main(["install", "--schedule"]) == 0
    assert schedule.plist_file().exists()


def test_load_failure_is_reported(fake_claude, mac, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_LAUNCHCTL_EXIT", "5")
    assert main(["install"]) == 1
    assert "couldn't load the daily job" in capsys.readouterr().err


def test_not_on_macos(fake_claude, monkeypatch, capsys):
    monkeypatch.setattr("sys.platform", "linux")
    assert main(["install"]) == 0
    assert calls("launchctl") == [] and not schedule.plist_file().exists()
    assert "macOS only for now" in capsys.readouterr().out


# --- when a scheduled run actually syncs -------------------------------------------

@pytest.mark.parametrize("now, ran, expected", [
    ("2026-11-20 03:00", "2026-11-19 03:00", True),   # the nightly run
    ("2026-11-20 09:00", "2026-11-20 03:00", False),  # login after it ran
    ("2026-11-20 09:00", "2026-11-19 03:00", True),   # Mac was off at 3am
    ("2026-11-21 03:00", "2026-11-20 09:00", True),   # catch-up run doesn't block the next night
    ("2026-11-20 02:00", "2026-11-19 03:05", False),  # before today's time: yesterday's run counts
    ("2026-11-20 02:00", None, True),
])
def test_due(now, ran, expected, home):
    if ran:
        schedule.mark_ran(datetime.fromisoformat(ran))
    assert schedule.due(datetime.fromisoformat(now), "03:00") is expected


def long_ago():
    schedule.mark_ran(datetime.now() - timedelta(days=2))


def test_scheduled_run_syncs_every_project(mem, fake_claude, agent_does, capsys, home):
    long_ago()
    note(mem)
    agent_does()
    assert main(["sync", "--scheduled"]) == 0
    assert len(agent_calls(fake_claude)) == 1
    assert (datetime.now() - schedule.last_run()).total_seconds() < 60
    assert calls("osascript") == []
    capsys.readouterr()
    assert main(["sync", "--scheduled"]) == 0  # e.g. at login: already ran
    assert "skipped: already ran" in capsys.readouterr().out
    assert len(agent_calls(fake_claude)) == 1


def test_scheduled_failure_notifies(mem, fake_claude, agent_does, monkeypatch):
    long_ago()
    note(mem, decisions="- [auth] Opaque IDs. Why: revocation.")
    agent_does()  # records no decision: the batch fails
    assert main(["sync", "--scheduled"]) == 1
    [[flag, script]] = calls("osascript")
    assert flag == "-e"
    assert 'Daily sync failed for repo. Run `hippo sync` in that repo to see why.' in script
    assert 'with title "Hippo"' in script


def test_scheduled_when_logged_out_notifies(mem, fake_claude, monkeypatch, home):
    long_ago()
    monkeypatch.setenv("FAKE_CLAUDE_LOGGED_IN", "0")
    assert main(["sync", "--scheduled"]) == 1
    assert "logged out" in calls("osascript")[0][1]
    assert agent_calls(fake_claude) == []
    assert "daily sync skipped: Claude Code is logged out" in today_log(home)


def test_scheduled_skips_quietly_while_another_sync_runs(mem, fake_claude, home):
    long_ago()
    paths.lock_file().write_text(str(os.getpid()))
    assert main(["sync", "--scheduled"]) == 0
    assert calls("osascript") == []
    assert "already running" in today_log(home)


# --- model, turn limit and the run log ------------------------------------------------

def runs(home):
    f = home / "agent-memory/logs/agent-runs.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()]


def test_sync_uses_model_and_turn_limit_and_logs_each_run(mem, fake_claude, agent_does, home, git_repo):
    note(mem)
    git(git_repo, "commit", "-q", "--allow-empty", "-m", "nothing")
    (git_repo / "src/auth.py").write_text("y\n")
    git(git_repo, "commit", "-qam", "auth change")
    agent_does()
    assert main(["sync"]) == 0
    argvs = [c["argv"] for c in agent_calls(fake_claude)]
    assert len(argvs) == 2
    for argv in argvs:
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--max-turns") + 1] == "50"
    notes_run, catch_up_run = runs(home)
    assert notes_run["kind"] == "notes" and notes_run["notes"] == 1 and notes_run["outcome"] == "ok"
    assert catch_up_run["kind"] == "catch-up" and catch_up_run["commits"] == 1
    assert notes_run["model"] == "sonnet" and notes_run["max_turns"] == 50 and notes_run["turns"] == 1


def test_config_and_env_override(mem, fake_claude, agent_does, monkeypatch):
    cfg = config()
    cfg.update(sync_model="haiku", max_turns=12)
    paths.config_file().write_text(json.dumps(cfg))
    note(mem)
    agent_does()
    assert main(["sync"]) == 0
    argv = agent_calls(fake_claude)[-1]["argv"]
    assert argv[argv.index("--model") + 1] == "haiku" and argv[argv.index("--max-turns") + 1] == "12"
    note(mem, name="2026-10-21T101010")
    monkeypatch.setenv("HIPPO_MODEL", "opus")
    assert main(["sync"]) == 0
    argv = agent_calls(fake_claude)[-1]["argv"]
    assert argv[argv.index("--model") + 1] == "opus"


def test_hitting_the_turn_limit_fails_safely(mem, fake_claude, agent_does, monkeypatch, capsys, home):
    note(mem)
    agent_does()
    monkeypatch.setenv("FAKE_CLAUDE_SUBTYPE", "error_max_turns")
    assert main(["sync"]) == 1
    assert "stopped at the 50-turn limit (max_turns in ~/agent-memory/config.json)" in capsys.readouterr().out
    assert (mem / "patches/2026-10-20T101010.md").exists()  # retried next time
    assert runs(home)[-1]["outcome"] == "hit_turn_limit"


def test_old_daily_logs_are_pruned(mem, fake_claude, home):
    logs = home / "agent-memory/logs"
    (logs / "2020-01-01.log").write_text("old\n")
    (logs / "agent-runs.jsonl").write_text("")
    assert main(["sync"]) == 0
    assert not (logs / "2020-01-01.log").exists()
    assert (logs / "agent-runs.jsonl").exists()
