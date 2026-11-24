import json
import os
import shlex
import time

import pytest

from hippo import paths, sync
from hippo.cli import main
from tests.conftest import git

NOTE = """---
author: Test
created_at: {ts}
commits: []
features: [{features}]
{extra}---

## Changed
{changed}

## Why
- [auth] Users asked.

## Decisions
{decisions}

## Follow-ups
None
{tail}"""


@pytest.fixture
def mem(fake_claude, git_repo, monkeypatch, capsys, home):
    for f in ("src/auth.py", "src/reports.py"):
        (git_repo / f).parent.mkdir(exist_ok=True)
        (git_repo / f).write_text("x\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-qm", "code")
    assert main(["install"]) == 0
    monkeypatch.chdir(git_repo)
    assert main(["link", "--no-init"]) == 0
    main(["feature", "set", "auth", "--name", "Auth", "--summary", "Sign in.", "--keyword", "a",
          "--keyword", "b", "--keyword", "c", "--key-file", "src/auth.py", "login"])
    uuid = json.loads((git_repo / ".hippo").read_text())["uuid"]
    d = home / "agent-memory" / "projects" / uuid
    (d / "features/auth/overview.md").write_text("# Auth\nOriginal overview.\n")
    (d / "features/auth/technical.md").write_text("# Auth\nOriginal technical.\n")
    capsys.readouterr()
    return d


def note(mem, name="2026-10-20T101010", decisions="None", new=None, changed="- [auth] Expiry is 7 days."):
    features, extra, tail = "auth", "", ""
    if new:
        features = f"auth, {new}"
        extra = f"new_features: [{new}]\n"
        changed += f"\n- [{new}] New CSV export."
        tail = f"\n## New features\n- [{new}] CSV reports.\n"
    ts = f"{name[:10]}T{name[11:13]}:{name[13:15]}:{name[15:17]}"
    (mem / "patches" / f"{name}.md").write_text(NOTE.format(
        ts=ts, features=features, extra=extra, changed=changed, decisions=decisions, tail=tail))
    return f"patches/{name}.md"


@pytest.fixture
def agent_does(tmp_path, monkeypatch):
    def script(*actions):
        f = tmp_path / "actions.json"
        f.write_text(json.dumps(list(actions)))
        monkeypatch.setenv("FAKE_CLAUDE_ACTIONS", str(f))
    return script


def hippo(home, *args):
    return {"bash": shlex.join([str(home / "agent-memory/bin/hippo"), *args])}


def edit(path, content):
    return {"write": path, "content": content}


def agent_calls(fake):
    return [c for c in fake.calls() if "-p" in c["argv"]]


def sync_now(*args):
    return main(["sync", *args])


def today_log(home):
    logs = list((home / "agent-memory/logs").glob("20*.log"))
    return logs[0].read_text() if logs else ""


# --- happy path -----------------------------------------------------------------

def test_nothing_to_do_never_calls_claude(mem, fake_claude, capsys, home):
    assert sync_now() == 0
    assert agent_calls(fake_claude) == []
    assert "no new patch notes" in capsys.readouterr().out
    assert "skipped: no new patch notes" in today_log(home)


def test_folds_note_into_docs(mem, fake_claude, agent_does, capsys, home):
    source = note(mem, decisions="- [auth] Opaque IDs. Why: revocation. Rejected: JWT.", new="reports")
    agent_does(
        edit("features/auth/overview.md", "# Auth\nSessions last 7 days.\n"),
        hippo(home, "feature", "set", "reports", "--name", "Reports", "--summary", "CSV reports.",
              "--keyword", "csv", "--keyword", "export", "--keyword", "usage",
              "--key-file", "src/reports.py", "export"),
        edit("features/reports/overview.md", "# Reports\nCSV.\n"),
        edit("features/reports/technical.md", "# Reports\nreports.py.\n"),
        hippo(home, "decision", "add", "auth", "--decision", "Opaque IDs.", "--why", "revocation.",
              "--rejected", "JWT", "--source", source),
    )
    start = json.loads((mem / "meta.json").read_text())["last_synced_commit"]
    assert sync_now() == 0

    assert not list((mem / "patches").glob("*.md"))
    assert (mem / "archive/patches/2026-10-20T101010.md").exists()
    assert "Sessions last 7 days" in (mem / "features/auth/overview.md").read_text()
    [d] = json.loads((mem / "features/auth/decisions.json").read_text())
    assert d["id"] == "auth-001" and d["date"] == "2026-10-20" and d["source"] == source
    assert "- reports (Reports)" in (mem / "index.md").read_text()
    meta = json.loads((mem / "meta.json").read_text())
    assert meta["last_consolidated_at"] is not None
    assert meta["last_synced_commit"] == start  # only the git safety net moves this
    assert list((mem / "archive").glob("features-before-sync-*"))
    out = capsys.readouterr().out
    assert "1 note(s), 1 decision(s)" in out
    assert "save    decision for auth" in out
    assert "succeeded: 1 note(s), 1 decision(s)" in today_log(home)


# --- failures restore everything ----------------------------------------------------

def assert_untouched(mem):
    assert (mem / "features/auth/overview.md").read_text() == "# Auth\nOriginal overview.\n"
    assert (mem / "patches/2026-10-20T101010.md").exists()
    assert json.loads((mem / "meta.json").read_text())["last_consolidated_at"] is None


def test_missing_decision_fails_and_restores(mem, agent_does, capsys, home):
    note(mem, decisions="- [auth] Opaque IDs. Why: revocation.")
    agent_does(edit("features/auth/overview.md", "# Auth\nHalf-done edit.\n"))
    assert sync_now() == 1
    assert_untouched(mem)
    out = capsys.readouterr().out
    assert "has 1 decision(s) but 0 were recorded" in out
    assert "feature docs restored" in out
    assert "failed:" in today_log(home)


def test_missing_new_feature_fails(mem, agent_does, capsys):
    note(mem, new="reports")
    agent_does(edit("features/auth/overview.md", "# Auth\nedit\n"))
    assert sync_now() == 1
    assert_untouched(mem)
    assert "new feature 'reports' from 2026-10-20T101010.md wasn't created" in capsys.readouterr().out


def test_agent_error_restores(mem, agent_does, monkeypatch):
    note(mem)
    agent_does(edit("features/auth/overview.md", "# Auth\nedit\n"))
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    assert sync_now() == 1
    assert_untouched(mem)


def test_retry_after_failure_records_decision_once(mem, agent_does, home, monkeypatch):
    source = note(mem, decisions="- [auth] Opaque IDs. Why: revocation.")
    add = hippo(home, "decision", "add", "auth", "--decision", "Opaque IDs.", "--why", "r",
                "--source", source)
    agent_does(add)
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")  # decision added, then the agent fails
    assert sync_now() == 1
    assert not (mem / "features/auth/decisions.json").exists()  # restored with features/
    monkeypatch.delenv("FAKE_CLAUDE_EXIT")
    assert sync_now() == 0
    assert len(json.loads((mem / "features/auth/decisions.json").read_text())) == 1


# --- batching and snapshot ------------------------------------------------------------

def test_large_backlog_runs_in_batches(mem, fake_claude, agent_does):
    for i in range(25):
        note(mem, name=f"2026-10-20T10{i:04d}")
    agent_does()
    assert sync_now() == 0
    assert len(agent_calls(fake_claude)) == 2
    assert len(list((mem / "archive/patches").glob("*.md"))) == 25


def test_notes_written_during_sync_wait(mem, agent_does):
    note(mem)
    late = NOTE.format(ts="2026-10-21T00:00:00", features="auth", extra="",
                       changed="- [auth] Later.", decisions="None", tail="")
    agent_does(edit("patches/2026-10-21T000000.md", late))
    assert sync_now() == 0
    assert (mem / "patches/2026-10-21T000000.md").exists()
    assert (mem / "archive/patches/2026-10-20T101010.md").exists()


def test_backups_pruned(mem, agent_does):
    agent_does()
    for i in range(sync.KEEP_BACKUPS + 2):
        note(mem, name=f"2026-10-2{i}T101010")
        assert sync_now() == 0
    assert len(list((mem / "archive").glob("features-before-sync-*"))) == sync.KEEP_BACKUPS


# --- sandbox and prompt ------------------------------------------------------------------

def test_agent_sandbox_and_prompt(mem, fake_claude, agent_does, home, git_repo):
    note(mem, decisions="- [auth] Opaque IDs. Why: revocation.")
    agent_does()
    sync_now()
    [call] = agent_calls(fake_claude)
    rules = call["settings"]["permissions"]
    h = home / "agent-memory/bin/hippo"
    for prefix in ("feature set", "decision add", "decision supersede"):
        assert f"Bash({h} {prefix} *)" in rules["allow"]
    assert f"Edit(/{mem}/features/**)" in rules["allow"]
    for f in ("features/*/feature.json", "features/*/decisions.json", "index.md", "patches/**"):
        assert f"Edit(/{mem}/{f})" in rules["deny"]
    assert f"Edit(/{git_repo.resolve()}/**)" in rules["deny"]
    assert call["cwd"] == str(mem) and call["hippo_agent"] == "1"
    prompt = call["stdin"]
    assert "### patches/2026-10-20T101010.md" in prompt and "Opaque IDs" in prompt
    assert "- auth (Auth): Sign in." in prompt
    # Current files of touched features are inlined, so the agent needn't read them.
    assert "### features/auth/feature.json" in prompt and '"summary": "Sign in."' in prompt
    assert "### features/auth/overview.md" in prompt and "Original overview." in prompt
    assert "Original technical." in prompt
    assert "{{" not in prompt


def test_inlines_only_touched_existing_features(mem, fake_claude, agent_does, home):
    main(["feature", "set", "billing", "--name", "Billing", "--summary", "Charges.",
          "--keyword", "a", "--keyword", "b", "--keyword", "c", "--key-file", "src/auth.py", "x"])
    (mem / "features/auth/decisions.json").write_text('[{"id": "auth-001", "decision": "Opaque IDs."}]')
    note(mem, new="reports")
    agent_does()
    sync_now()
    prompt = agent_calls(fake_claude)[0]["stdin"]
    assert "### features/auth/decisions.json" in prompt and "Opaque IDs." in prompt
    assert "features/billing/feature.json" not in prompt  # not named by the note
    assert "features/reports/" not in prompt.split("## Notes to fold in")[0]  # new: nothing yet


def test_huge_doc_is_truncated(mem, fake_claude, agent_does, monkeypatch):
    monkeypatch.setattr(sync, "DOC_LIMIT", 50)
    (mem / "features/auth/technical.md").write_text("# Auth\n" + "x" * 500)
    note(mem)
    agent_does()
    sync_now()
    assert "(truncated; read the file for the rest)" in agent_calls(fake_claude)[0]["stdin"]


# --- several projects, lock, housekeeping ------------------------------------------------

def test_all_skips_missing_repo(mem, agent_does, home, capsys):
    reg = home / "agent-memory/projects.json"
    entries = json.loads(reg.read_text()) + [{"uuid": "gone", "repo_path": "/nowhere/old-repo"}]
    reg.write_text(json.dumps(entries))
    note(mem)
    agent_does()
    assert sync_now("--all") == 0
    out = capsys.readouterr().out
    assert "old-repo: repo not found" in out and "(memory kept)" in out
    assert (mem / "archive/patches/2026-10-20T101010.md").exists()


def test_lock_held_by_live_process(mem, capsys):
    paths.lock_file().write_text(str(os.getpid()))
    assert sync_now() == 1
    assert "already running" in capsys.readouterr().err
    paths.lock_file().unlink()


def test_stale_lock_taken_over(mem):
    paths.lock_file().write_text("999999")
    assert sync_now() == 0
    assert not paths.lock_file().exists()


def test_old_session_state_cleaned(mem, home):
    state = home / "agent-memory/state" / mem.name
    state.mkdir(parents=True)
    old, fresh = state / "old.json", state / "fresh.json"
    old.write_text("{}")
    fresh.write_text("{}")
    past = time.time() - sync.STALE_STATE_SECONDS - 60
    os.utime(old, (past, past))
    sync_now()
    assert not old.exists() and fresh.exists()


def test_refused_inside_hippo_agent(mem, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_AGENT", "1")
    assert sync_now() == 1
    assert "Hippo's own agents" in capsys.readouterr().err
