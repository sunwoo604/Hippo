import io
import json
import textwrap
from datetime import datetime

import pytest

from hippo import patches
from hippo.cli import main
from hippo.memory import Memory, frontmatter
from tests.conftest import git

BODY = """
## Changed
- [auth] Session expiry moved from 24h to 7d.
## Why
- [auth] Users were logged out mid-task.
## Decisions
None
## Follow-ups
None
"""


@pytest.fixture
def project(fake_claude, git_repo, monkeypatch, capsys, home):
    for f in ("src/auth/login.ts", "src/billing/charge.ts", "src/reports/x.ts"):
        (git_repo / f).parent.mkdir(parents=True, exist_ok=True)
        (git_repo / f).write_text("x\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-qm", "code")
    assert main(["install"]) == 0
    monkeypatch.chdir(git_repo)
    assert main(["link", "--no-init"]) == 0
    for slug, path in (("auth", "src/auth/**"), ("billing", "src/billing/charge.ts")):
        assert main(["feature", "set", slug, "--name", slug.title(), "--summary", "S.",
                     "--keyword", "a", "--keyword", "b", "--keyword", "c",
                     "--key-file", path, "role"]) == 0
    capsys.readouterr()
    uuid = json.loads((git_repo / ".hippo").read_text())["uuid"]
    return home / "agent-memory" / "projects" / uuid


def patch(monkeypatch, body, *args):
    monkeypatch.setattr("sys.stdin", io.StringIO(textwrap.dedent(body)))
    return main(["patch", "new", *args])


def notes(mem):
    return sorted((mem / "patches").glob("*.md"))


def err(capsys):
    return capsys.readouterr().err


# --- happy paths --------------------------------------------------------------

def test_single_feature(project, monkeypatch, capsys):
    assert patch(monkeypatch, BODY, "--feature", "auth") == 0
    [note] = notes(project)
    text = note.read_text()
    meta = frontmatter(text)
    assert meta["author"] == "Test"
    assert meta["commits"] == [] and meta["features"] == ["auth"]
    assert "new_features" not in meta
    assert text.index("## Changed") < text.index("## Why") < text.index("## Decisions") < text.index("## Follow-ups")
    assert "## Decisions\nNone" in text
    assert "created   patches/" in capsys.readouterr().out


def test_multi_feature_with_commits(project, git_repo, monkeypatch):
    full = git(git_repo, "rev-parse", "HEAD")
    body = """
    ## Changed
    - [auth] Expiry is now 7 days.
    - [billing] Reads active sessions.
    ## Why
    - [auth] Users were logged out.
    - [billing] Login counts no longer match active users.
    ## Decisions
    - [auth] Sliding 7-day sessions. Why: simpler, no client storage.
      Rejected: refresh tokens in localStorage. Also affects: billing.
    ## Follow-ups
    - [auth] Add a refresh test.
    """
    assert patch(monkeypatch, body, "--feature", "auth", "--feature", "billing",
                 "--commit", full) == 0
    [note] = notes(project)
    text = note.read_text()
    meta = frontmatter(text)
    assert meta["features"] == ["auth", "billing"]
    assert meta["commits"] == [full[:7]]
    # continuation line folded into its bullet
    assert "Why: simpler, no client storage. Rejected: refresh tokens" in text


def test_new_feature_then_pending_reuse(project, monkeypatch, capsys):
    body = """
    ## Changed
    - [reports] New CSV export.
    - [billing] Exposes usage totals.
    ## Why
    - [reports] Admins asked for monthly usage.
    ## Decisions
    None
    ## Follow-ups
    None
    """
    assert patch(monkeypatch, body, "--feature", "billing",
                 "--new-feature", "reports", "Monthly usage reports as CSV.") == 0
    [note] = notes(project)
    text = note.read_text()
    meta = frontmatter(text)
    assert meta["features"] == ["billing", "reports"]
    assert meta["new_features"] == ["reports"]
    assert "## New features\n- [reports] Monthly usage reports as CSV." in text

    capsys.readouterr()
    main(["open"])
    assert "reports   (new, added at the next consolidation)" in capsys.readouterr().out

    # A later session may use the pending feature with --feature...
    later = BODY.replace("[auth]", "[reports]")
    assert patch(monkeypatch, later, "--feature", "reports") == 0
    # ...but can't declare it new again.
    capsys.readouterr()
    assert patch(monkeypatch, later, "--new-feature", "reports", "Again.") == 1
    assert "already declared by another unmerged patch" in err(capsys)


def test_round_trips_through_memory_reader(project, monkeypatch):
    patch(monkeypatch, BODY, "--feature", "auth")
    [p] = Memory(project).patches()
    assert p.features == ["auth"]


def test_works_from_subfolder(project, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo / "src" / "auth")
    assert patch(monkeypatch, BODY, "--feature", "auth") == 0


def test_filename_collision_gets_suffix(project, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 17, 14, 30, 12)
    monkeypatch.setattr(patches, "datetime", Frozen)
    patch(monkeypatch, BODY, "--feature", "auth")
    patch(monkeypatch, BODY, "--feature", "auth")
    assert [n.name for n in notes(project)] == ["2026-10-17T143012-2.md", "2026-10-17T143012.md"]


# --- feature checks -----------------------------------------------------------

def test_unknown_feature_suggests(project, monkeypatch, capsys):
    assert patch(monkeypatch, BODY.replace("[auth]", "[auht]"), "--feature", "auht") == 1
    e = err(capsys)
    assert "did you mean 'auth'" in e and "--new-feature auht" in e
    assert not notes(project)


@pytest.mark.parametrize("slug,desc,problem", [
    ("auth", "x", "already exists; use --feature auth"),
    ("billings", "x", "looks like existing feature 'billing'"),
    ("Bad_Slug", "x", "lowercase kebab-case"),
    ("reports", "  ", "needs a one-line description"),
])
def test_new_feature_checks(project, monkeypatch, capsys, slug, desc, problem):
    body = BODY.replace("[auth]", f"[{slug}]")
    assert patch(monkeypatch, body, "--new-feature", slug, desc) == 1
    assert problem in err(capsys)


def test_needs_some_feature(project, monkeypatch, capsys):
    assert patch(monkeypatch, BODY) == 1
    assert "name at least one --feature" in err(capsys)


# --- body checks ----------------------------------------------------------------

@pytest.mark.parametrize("body,problem", [
    (BODY.replace("- [auth] Session", "- Session"), "tag the item with its feature"),
    (BODY.replace("- [auth] Session", "- [billing] Session"), "has no '- [auth] ...' line under '## Changed'"),
    (BODY + "- [billing] stray\n", "'[billing]' under '## Follow-ups' isn't one of"),
    (BODY.replace("## Follow-ups\nNone\n", ""), "missing section '## Follow-ups'"),
    (BODY + "## Notes\nx\n", "unknown section '## Notes'"),
    (BODY.replace("- [auth] Users were logged out mid-task.", "None"), "'## Why' can't be empty"),
    (BODY.replace("## Decisions\nNone", "## Decisions\n- [auth] Use JWT."), "needs a 'Why: ...'"),
    (BODY.replace("- [auth] Session expiry", "Session expiry"), "start each item with '- [feature] '"),
    ("intro\n" + BODY, "text before the first section"),
])
def test_body_checks(project, monkeypatch, capsys, body, problem):
    assert patch(monkeypatch, body, "--feature", "auth") == 1
    assert problem in err(capsys)
    assert not notes(project)


def test_all_problems_reported_together(project, monkeypatch, capsys):
    assert patch(monkeypatch, "## Changed\n- untagged\n", "--feature", "nope") == 1
    e = err(capsys)
    for problem in ("doesn't exist", "tag the item", "missing section '## Why'"):
        assert problem in e


def test_unknown_commit(project, monkeypatch, capsys):
    assert patch(monkeypatch, BODY, "--feature", "auth", "--commit", "deadbeef") == 1
    assert "isn't a commit in this repo" in err(capsys)


# --- warnings and guards ----------------------------------------------------------

def test_warns_about_changed_files_of_unnamed_feature(project, git_repo, monkeypatch, capsys):
    # billing's file sorts first in git's output: the first line must parse too.
    (git_repo / "src/billing/charge.ts").write_text("changed\n")
    (git_repo / "src/auth/login.ts").write_text("changed\n")
    (git_repo / "src/auth/new.ts").write_text("untracked\n")
    assert patch(monkeypatch, BODY, "--feature", "auth") == 0
    out = capsys.readouterr().out
    assert "warning   src/billing/charge.ts changed and belong to 'billing'" in out
    assert "'auth'" not in out.split("warning")[1]


def test_refused_inside_hippo_agent(project, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_AGENT", "1")
    assert patch(monkeypatch, BODY, "--feature", "auth") == 1
    assert "not Hippo's own agents" in err(capsys)


def test_needs_stdin(project, monkeypatch, capsys):
    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", Tty(""))
    assert main(["patch", "new", "--feature", "auth"]) == 1
    assert "pipe the note's body on stdin" in err(capsys)
