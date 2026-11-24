import io
import json
import textwrap
from pathlib import Path

import pytest

from hippo import git as hgit
from hippo import safety
from hippo.cli import main
from hippo.project import Project
from tests.conftest import git
from tests.test_sync import agent_calls, agent_does, edit, hippo, mem, note, sync_now, today_log  # noqa: F401

BODY = """
## Changed
- [auth] Expiry is 7 days.
## Why
- [auth] Users asked.
## Decisions
None
## Follow-ups
None
"""


def commit(repo, files: dict, msg="change"):
    for path, text in files.items():
        f = repo / path
        if text is None:
            f.unlink()
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
    git(repo, "add", "-A", *files)
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


def meta(mem):
    return json.loads((mem / "meta.json").read_text())


def catch_up_calls(fake):
    return [c for c in agent_calls(fake) if "Hippo catch-up agent" in c["stdin"]]


def items(mem, status="open"):
    return [i for i in json.loads((mem / "needs-review.json").read_text()) if i["status"] == status]


def project(mem, repo):
    return Project(mem.name, repo)


# --- commits without notes ------------------------------------------------------------

def test_unexplained_commit_updates_docs_from_a_snapshot(mem, git_repo, fake_claude, agent_does, capsys, home):
    tip = commit(git_repo, {"src/auth.py": "def login(): return 7\n"}, "Shorter sessions")
    agent_does(edit("features/auth/overview.md", "# Auth\nSessions last 7 days (changed without session notes).\n"))
    assert sync_now() == 0

    [call] = catch_up_calls(fake_claude)
    prompt = call["stdin"]
    assert "Shorter sessions" in prompt
    assert "- auth: src/auth.py" in prompt
    assert "+def login(): return 7" in prompt
    assert "Original overview." in prompt  # touched feature's docs inlined
    snap = Path(call["argv"][call["argv"].index("--add-dir") + 1])
    assert snap.parent == home / "agent-memory" / "tmp"
    assert str(git_repo) not in call["argv"]  # the working copy isn't given to the agent
    assert not snap.exists()  # deleted afterwards
    allow, deny = call["settings"]["permissions"]["allow"], call["settings"]["permissions"]["deny"]
    assert any("review add" in a for a in allow) and not any("decision add" in a for a in allow)
    assert f"Edit(/{snap}/**)" in deny and f"Read(/{snap}/**/.env*)" in deny

    assert "changed without session notes" in (mem / "features/auth/overview.md").read_text()
    assert meta(mem)["last_synced_commit"] == tip
    out = capsys.readouterr().out
    assert "1 commit(s) on main without session notes" in out
    assert "safety net: 1 new commit(s)" in today_log(home)


def test_second_sync_has_nothing_to_do(mem, git_repo, fake_claude, agent_does):
    commit(git_repo, {"src/auth.py": "y\n"})
    agent_does()
    assert sync_now() == 0
    assert sync_now() == 0
    assert len(catch_up_calls(fake_claude)) == 1


def test_commit_listed_in_a_note_is_explained(mem, git_repo, fake_claude, agent_does):
    tip = commit(git_repo, {"src/auth.py": "y\n"})
    f = mem / note(mem)
    f.write_text(f.read_text().replace("commits: []", f"commits: [{tip[:7]}]"))
    agent_does()
    assert sync_now() == 0
    assert len(agent_calls(fake_claude)) == 1  # the notes pass only
    assert catch_up_calls(fake_claude) == []
    assert meta(mem)["last_synced_commit"] == tip


def test_commit_of_already_noted_content_is_explained(mem, git_repo, fake_claude, agent_does, monkeypatch, capsys):
    # The session writes its note first; the work is committed later (or squash-merged).
    (git_repo / "src/auth.py").write_text("noted\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(textwrap.dedent(BODY)))
    assert main(["patch", "new", "--feature", "auth"]) == 0
    noted = json.loads((mem / "noted-files.json").read_text())
    assert list(noted["src/auth.py"].values()) == [next((mem / "patches").glob("*.md")).name]
    git(git_repo, "add", "src/auth.py")
    git(git_repo, "commit", "-qm", "commit the noted work")
    agent_does()
    assert sync_now() == 0
    assert catch_up_calls(fake_claude) == []
    # Changing the file again afterwards is new, unexplained work.
    commit(git_repo, {"src/auth.py": "changed again\n"})
    assert sync_now() == 0
    assert len(catch_up_calls(fake_claude)) == 1


def test_noise_and_secret_only_commits_are_skipped(mem, git_repo, fake_claude, agent_does):
    tip = commit(git_repo, {"package-lock.json": "{}\n", ".env": "SECRET=hunter2\n",
                            "web/dist/app.js": "x\n"})
    agent_does()
    assert sync_now() == 0
    assert catch_up_calls(fake_claude) == []
    assert meta(mem)["last_synced_commit"] == tip
    assert items(mem) == []


def test_secrets_never_reach_the_prompt_or_snapshot(mem, git_repo, fake_claude, agent_does):
    tip = commit(git_repo, {"src/auth.py": "y\n", ".env": "SECRET=hunter2\n",
                            "config/secrets/prod.json": "hunter2\n", "yarn.lock": "lock\n"})
    agent_does()
    assert sync_now() == 0
    [call] = catch_up_calls(fake_claude)
    assert "hunter2" not in call["stdin"] and ".env" not in call["stdin"]
    assert "yarn.lock" not in call["stdin"]
    with safety.snapshot(project(mem, git_repo), tip) as snap:
        assert (snap / "src/auth.py").read_text() == "y\n"
        assert not (snap / ".env").exists()
        assert not (snap / "config/secrets").exists()
    assert not snap.exists()


def test_batches_of_thirty(mem, git_repo, fake_claude, agent_does):
    for i in range(31):
        commit(git_repo, {"src/auth.py": f"{i}\n"})
    agent_does()
    assert sync_now() == 0
    assert len(catch_up_calls(fake_claude)) == 2


# --- the agent stays in its lane --------------------------------------------------------

def test_agent_creating_a_feature_is_undone(mem, git_repo, agent_does, capsys, home):
    start = meta(mem)["last_synced_commit"]
    commit(git_repo, {"src/auth.py": "y\n"})
    agent_does(
        edit("features/auth/overview.md", "# Auth\nchanged\n"),
        hippo(home, "feature", "set", "reports", "--name", "Reports", "--summary", "CSV.",
              "--keyword", "a", "--keyword", "b", "--keyword", "c",
              "--key-file", "src/reports.py", "export"),
    )
    assert sync_now() == 1
    assert not (mem / "features/reports").exists()
    assert (mem / "features/auth/overview.md").read_text() == "# Auth\nOriginal overview.\n"
    assert meta(mem)["last_synced_commit"] == start
    out = capsys.readouterr().out
    assert "feature 'reports' was created" in out
    assert "safety net failed" in today_log(home)


def test_agent_failure_keeps_commits_for_next_time(mem, git_repo, agent_does, monkeypatch, fake_claude):
    start = meta(mem)["last_synced_commit"]
    commit(git_repo, {"src/auth.py": "y\n"})
    agent_does()
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    assert sync_now() == 1
    assert meta(mem)["last_synced_commit"] == start
    monkeypatch.delenv("FAKE_CLAUDE_EXIT")
    assert sync_now() == 0
    assert len(catch_up_calls(fake_claude)) == 2


# --- review items -------------------------------------------------------------------------

def test_unclaimed_files_become_a_review_item_without_the_agent(mem, git_repo, fake_claude, agent_does, capsys, home):
    c = commit(git_repo, {"src/payments/api.py": "x\n", "src/payments/models.py": "y\n"})
    agent_does()
    assert sync_now() == 0
    assert catch_up_calls(fake_claude) == []
    [item] = items(mem)
    assert item["kind"] == "unclaimed" and item["path"] == "src/payments/"
    assert item["files"] == ["src/payments/api.py", "src/payments/models.py"]
    assert item["commits"] == [c[:7]]
    capsys.readouterr()

    assert main(["open"]) == 0
    assert "Needs review       1 item   hippo open --review" in capsys.readouterr().out
    assert main(["open", "--review"]) == 0
    out = capsys.readouterr().out
    assert "r-001" in out and "2 files in src/payments/ changed without a session note" in out

    # A feature now claims the files: the item closes itself at the next sync.
    assert main(["feature", "set", "payments", "--name", "Payments", "--summary", "Pay.",
                 "--keyword", "a", "--keyword", "b", "--keyword", "c",
                 "--key-file", "src/payments/*.py", "code"]) == 0
    assert sync_now() == 0
    assert items(mem) == []
    assert items(mem, "resolved")[0]["id"] == "r-001"


def test_missing_key_file_is_flagged_and_resolves(mem, git_repo, agent_does, monkeypatch):
    commit(git_repo, {"src/auth.py": None}, "remove auth")
    agent_does()
    assert sync_now() == 0
    [item] = items(mem)
    assert item["kind"] == "missing-key-file" and item["feature"] == "auth"
    assert "key file src/auth.py isn't on main" in item["detail"]
    commit(git_repo, {"src/auth.py": "back\n"}, "restore auth")
    assert sync_now() == 0
    assert items(mem) == []


def test_dismissed_items_stay_dismissed(mem, git_repo, agent_does, capsys):
    commit(git_repo, {"docs/guide.md": "x\n"})
    agent_does()
    assert sync_now() == 0
    assert main(["review", "done", "r-001"]) == 0
    assert "dismissed" in capsys.readouterr().out
    commit(git_repo, {"docs/guide.md": "y\n"})
    assert sync_now() == 0
    assert items(mem) == []
    assert len(items(mem, "dismissed")) == 1
    assert main(["review", "done", "r-999"]) == 1


def test_review_add_is_for_hippo_agents(mem, monkeypatch, capsys):
    assert main(["review", "add", "Should auth be split?"]) == 1
    assert "Hippo's own agents" in capsys.readouterr().err
    monkeypatch.setenv("HIPPO_AGENT", "1")
    monkeypatch.chdir(mem)
    assert main(["review", "add", "--feature", "auth", "Should auth be split?"]) == 0
    assert main(["review", "add", "--feature", "auth", "Should auth be split?"]) == 0  # no duplicate
    assert main(["review", "add", "--feature", "nope", "x"]) == 1
    assert main(["review", "done", "r-001"]) == 1  # only the user dismisses
    [item] = items(mem)
    assert item["kind"] == "agent" and item["feature"] == "auth"


# --- the range ---------------------------------------------------------------------------

def test_unreachable_start_warns_and_recovers(mem, git_repo, agent_does, capsys, home):
    m = meta(mem)
    m["last_synced_commit"] = "deadbeef" * 5
    (mem / "meta.json").write_text(json.dumps(m))
    tip = commit(git_repo, {"src/auth.py": "y\n"})
    agent_does()
    assert sync_now() == 0
    assert "no longer on the branch" in capsys.readouterr().out
    assert meta(mem)["last_synced_commit"] == tip


def test_tip_is_whichever_of_local_and_origin_is_ahead(git_repo):
    base = git(git_repo, "rev-parse", "HEAD")
    assert hgit.branch_tip(git_repo, "main") == base
    git(git_repo, "checkout", "-qb", "other")
    ahead = commit(git_repo, {"a.txt": "a\n"})
    git(git_repo, "update-ref", "refs/remotes/origin/main", ahead)
    assert hgit.branch_tip(git_repo, "main") == ahead  # merged on GitHub, not pulled
    git(git_repo, "checkout", "-q", "main")
    local = commit(git_repo, {"b.txt": "b\n"})
    assert hgit.branch_tip(git_repo, "main") == local  # diverged: local wins


def test_progress_shows_failed_tool_calls(capsys):
    from hippo import agent
    events = [{"type": "assistant", "message": {"content": [
                  {"type": "tool_use", "name": "Bash", "input": {"command": "/h review add --feature x 'Hm'"}}]}},
              {"type": "user", "message": {"content": [
                  {"type": "tool_result", "is_error": True, "content": [{"type": "text", "text": "hippo: no feature named 'x'"}]}]}},
              {"type": "result"}]
    agent._read_stream(iter(json.dumps(e) for e in events), agent.progress(Path("/m"), Path("/r")))
    out = capsys.readouterr().out
    assert "flag    --feature x 'Hm'" in out
    assert "failed  hippo: no feature named 'x'" in out


def test_progress_shows_why_the_guard_blocked(capsys):
    from hippo import agent
    text = ("PreToolUse:Bash hook error: [/h _guard-bash --allow '/h feature set']: "
            "Blocked by Hippo: the only shell commands allowed here are `/h feature set ...`.")
    agent.progress(Path("/m"), Path("/r"))(agent.FAILED, {"text": text})
    assert "blocked Blocked by Hippo: the only shell commands allowed" in capsys.readouterr().out
