import io
import json
import textwrap

import pytest

from hippo import hooks
from hippo.cli import main
from tests.conftest import git

NOTE = """
## Changed
- [auth] Expiry is 7 days.
## Why
- [auth] Users were logged out.
## Decisions
None
## Follow-ups
None
"""


@pytest.fixture
def project(fake_claude, git_repo, monkeypatch, capsys, home):
    for f in ("src/auth/login.ts", "src/billing/charge.ts"):
        (git_repo / f).parent.mkdir(parents=True, exist_ok=True)
        (git_repo / f).write_text("x\n")
    (git_repo / ".gitignore").write_text(".hippo\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-qm", "code")
    assert main(["install"]) == 0
    monkeypatch.chdir(git_repo)
    assert main(["link", "--no-init"]) == 0
    for slug in ("auth", "billing"):
        assert main(["feature", "set", slug, "--name", slug.title(), "--summary", f"{slug} things.",
                     "--keyword", "a", "--keyword", "b", "--keyword", "c",
                     "--key-file", f"src/{slug}/**", "code"]) == 0
    capsys.readouterr()
    uuid = json.loads((git_repo / ".hippo").read_text())["uuid"]
    return home / "agent-memory" / "projects" / uuid


def fire(monkeypatch, capsys, event, repo, **fields):
    payload = {"session_id": "s1", "cwd": str(repo), **fields}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(["_hook", event]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def write_note(monkeypatch, capsys, *args):
    monkeypatch.setattr("sys.stdin", io.StringIO(textwrap.dedent(NOTE)))
    assert main(["patch", "new", "--feature", "auth", *args]) == 0
    capsys.readouterr()


def change(repo, path="src/auth/login.ts", text="changed\n"):
    (repo / path).write_text(text)


# --- session-start --------------------------------------------------------------

def test_session_start_injects_memory(project, git_repo, monkeypatch, capsys, home):
    out = fire(monkeypatch, capsys, "session-start", git_repo, source="startup")
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "- auth (Auth): auth things." in ctx and "- billing (Billing)" in ctx
    assert str(project) in ctx
    assert f"{home}/agent-memory/bin/hippo patch new" in ctx
    assert "Unmerged patch notes" in ctx and "None." in ctx
    assert "{{" not in ctx


def test_session_start_lists_notes_and_pending_features(project, git_repo, monkeypatch, capsys):
    write_note(monkeypatch, capsys)
    body = NOTE.replace("[auth]", "[reports]")
    monkeypatch.setattr("sys.stdin", io.StringIO(textwrap.dedent(body)))
    main(["patch", "new", "--new-feature", "reports", "CSV reports."])
    capsys.readouterr()
    ctx = fire(monkeypatch, capsys, "session-start", git_repo)["hookSpecificOutput"]["additionalContext"]
    assert "(auth): [auth] Expiry is 7 days." in ctx
    assert "- reports: new, declared in an unmerged patch note" in ctx


def test_context_stays_under_limit(project, git_repo, monkeypatch, capsys):
    for i in range(300):
        (project / "patches" / f"2026-01-01T{i:06d}.md").write_text(
            "---\nfeatures: [auth]\n---\n## Changed\n- [auth] " + "x" * 100 + "\n")
    ctx = fire(monkeypatch, capsys, "session-start", git_repo)["hookSpecificOutput"]["additionalContext"]
    assert len(ctx) <= hooks.CONTEXT_LIMIT
    assert "older notes in patches/" in ctx


@pytest.mark.parametrize("setup", ["unlinked", "agent"])
def test_silent_outside_linked_repos_and_in_hippo_agents(
        project, git_repo, tmp_path, monkeypatch, capsys, setup):
    repo = git_repo
    if setup == "unlinked":
        repo = tmp_path / "other"
        repo.mkdir()
        git(repo, "init", "-q")
    else:
        monkeypatch.setenv("HIPPO_AGENT", "1")
    for event in hooks.EVENTS:
        assert fire(monkeypatch, capsys, event, repo) is None


# --- stop -------------------------------------------------------------------------

def test_stop_quiet_without_changes(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo)
    assert fire(monkeypatch, capsys, "stop", git_repo) is None


def test_stop_asks_for_note_with_features_and_commits(project, git_repo, monkeypatch, capsys, home):
    fire(monkeypatch, capsys, "session-start", git_repo)
    change(git_repo)
    git(git_repo, "commit", "-qam", "7d sessions")
    sha = git(git_repo, "rev-parse", "--short=7", "HEAD")
    change(git_repo, "src/billing/charge.ts")  # uncommitted too
    out = fire(monkeypatch, capsys, "stop", git_repo)
    assert out["decision"] == "block"
    reason = out["reason"]
    assert f"{home}/agent-memory/bin/hippo patch new --feature auth --feature billing --commit {sha} <<'EOF'" in reason
    assert "Known features: auth, billing" in reason
    assert "only choices the user explicitly agreed to" in reason
    assert "If the task is done" in reason
    assert "reply only \"Still mid-task; I'll write the note once it's done.\"" in reason


def test_stop_accepts_nothing_meaningful_once(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo)
    change(git_repo)
    assert fire(monkeypatch, capsys, "stop", git_repo)["decision"] == "block"
    # Claude replies "nothing meaningful" and stops again: let it, and remember.
    assert fire(monkeypatch, capsys, "stop", git_repo, stop_hook_active=True) is None
    assert fire(monkeypatch, capsys, "stop", git_repo) is None
    # Something new changes later: ask again.
    change(git_repo, text="again\n")
    assert fire(monkeypatch, capsys, "stop", git_repo)["decision"] == "block"


def test_stop_satisfied_by_a_note(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo)
    change(git_repo)
    assert fire(monkeypatch, capsys, "stop", git_repo)["decision"] == "block"
    write_note(monkeypatch, capsys)
    assert fire(monkeypatch, capsys, "stop", git_repo) is None
    assert fire(monkeypatch, capsys, "stop", git_repo) is None


def test_committing_noted_work_does_not_ask_again(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo)
    change(git_repo)
    write_note(monkeypatch, capsys)
    assert fire(monkeypatch, capsys, "stop", git_repo) is None
    git(git_repo, "commit", "-qam", "commit the noted work")
    assert fire(monkeypatch, capsys, "stop", git_repo) is None
    assert fire(monkeypatch, capsys, "session-end", git_repo) is None
    assert not list((project / "unnoted").glob("*.json"))
    change(git_repo, text="more\n")  # new work after the commit is still noticed
    assert fire(monkeypatch, capsys, "session-start", git_repo, source="startup") is not None
    change(git_repo, text="even more\n")
    assert fire(monkeypatch, capsys, "stop", git_repo)["decision"] == "block"


def test_stop_skips_commits_already_in_notes(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo)
    change(git_repo)
    git(git_repo, "commit", "-qam", "one")
    first = git(git_repo, "rev-parse", "--short=7", "HEAD")
    write_note(monkeypatch, capsys, "--commit", first)
    fire(monkeypatch, capsys, "stop", git_repo)
    change(git_repo, text="two\n")
    git(git_repo, "commit", "-qam", "two")
    second = git(git_repo, "rev-parse", "--short=7", "HEAD")
    reason = fire(monkeypatch, capsys, "stop", git_repo)["reason"]
    assert f"--commit {second}" in reason and first not in reason


def test_stop_without_session_start_starts_watching(project, git_repo, monkeypatch, capsys):
    change(git_repo)
    assert fire(monkeypatch, capsys, "stop", git_repo) is None  # baseline taken now
    change(git_repo, text="more\n")
    assert fire(monkeypatch, capsys, "stop", git_repo)["decision"] == "block"


def test_resume_keeps_original_start(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo, source="startup")
    change(git_repo)
    git(git_repo, "commit", "-qam", "work")
    sha = git(git_repo, "rev-parse", "--short=7", "HEAD")
    fire(monkeypatch, capsys, "session-start", git_repo, source="resume")
    assert f"--commit {sha}" in fire(monkeypatch, capsys, "stop", git_repo)["reason"]


# --- session-end ------------------------------------------------------------------

def markers(project):
    return list((project / "unnoted").glob("*.json"))


def test_session_end_marks_unnoted_changes(project, git_repo, monkeypatch, capsys, home):
    fire(monkeypatch, capsys, "session-start", git_repo)
    change(git_repo)
    git(git_repo, "commit", "-qam", "work")
    sha = git(git_repo, "rev-parse", "--short=7", "HEAD")
    fire(monkeypatch, capsys, "session-end", git_repo, reason="prompt_input_exit",
         transcript_path="/t/s1.jsonl")
    [m] = markers(project)
    data = json.loads(m.read_text())
    assert data["unnoted_commits"] == [sha]
    assert "src/auth/login.ts" in data["changed_files"]
    assert data["transcript_path"] == "/t/s1.jsonl" and data["end_reason"] == "prompt_input_exit"
    assert not list((home / "agent-memory" / "state").rglob("*.json"))


def test_session_end_quiet_when_noted(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo)
    change(git_repo)
    write_note(monkeypatch, capsys)
    fire(monkeypatch, capsys, "session-end", git_repo)
    assert not markers(project)


def test_session_end_marks_acknowledged_but_unnoted(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo)
    change(git_repo)
    fire(monkeypatch, capsys, "stop", git_repo)
    fire(monkeypatch, capsys, "stop", git_repo, stop_hook_active=True)
    fire(monkeypatch, capsys, "session-end", git_repo)
    assert len(markers(project)) == 1


def test_session_end_quiet_without_changes(project, git_repo, monkeypatch, capsys):
    fire(monkeypatch, capsys, "session-start", git_repo)
    fire(monkeypatch, capsys, "session-end", git_repo)
    assert not markers(project)


# --- never break the session ----------------------------------------------------------

def test_internal_error_is_logged_not_raised(project, git_repo, monkeypatch, capsys, home):
    fire(monkeypatch, capsys, "session-start", git_repo)
    [state] = (home / "agent-memory" / "state").rglob("*.json")
    state.write_text("{corrupt")
    change(git_repo)
    assert fire(monkeypatch, capsys, "stop", git_repo) is None
    assert "JSONDecodeError" in (home / "agent-memory" / "logs" / "hooks.log").read_text()


def test_garbage_stdin_is_harmless(project, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["_hook", "stop"]) == 0
    assert capsys.readouterr().out == ""
