import json
import re
import shutil
import subprocess

import pytest

from hippo import deps, git as hgit
from hippo.cli import main
from hippo.ui import NEXT_STEPS_HEADING
from tests.conftest import git

META_FIELDS = {
    "uuid", "repo_path", "remote_url", "default_branch",
    "created_at", "last_synced_commit", "last_consolidated_at",
}


@pytest.fixture
def installed(fake_claude, capsys):
    assert main(["install"]) == 0
    capsys.readouterr()


@pytest.fixture
def root(home):
    return home / "agent-memory"


def link(*args):
    return main(["link", "--no-init", *args])


def dot_uuid(repo):
    return json.loads((repo / ".hippo").read_text())["uuid"]


def clone(src, dest):
    subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True)
    return dest


def terminal(monkeypatch, *answers):
    replies = iter(answers)
    monkeypatch.setattr(deps, "is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))


# --- preconditions ----------------------------------------------------------

def test_requires_install(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert link() == 1
    err = capsys.readouterr().err
    assert "hippo install" in err.split(NEXT_STEPS_HEADING)[1]
    assert not (git_repo / ".hippo").exists()


def test_outside_git_repo(installed, tmp_path, monkeypatch, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    assert link() == 1
    assert "not inside a git repository" in capsys.readouterr().err


def test_already_linked(installed, git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    link()
    capsys.readouterr()
    assert link() == 1
    err = capsys.readouterr().err
    assert "already linked" in err and "hippo open" in err


# --- fresh link -------------------------------------------------------------

def test_fresh_link(installed, git_repo, monkeypatch, root, capsys):
    monkeypatch.chdir(git_repo)
    assert link() == 0

    uuid = dot_uuid(git_repo)
    pdir = root / "projects" / uuid
    for d in ("features", "patches", "archive"):
        assert (pdir / d).is_dir()
    assert (pdir / "needs-review.json").read_text() == "[]\n"

    meta = json.loads((pdir / "meta.json").read_text())
    assert set(meta) == META_FIELDS
    assert meta["uuid"] == uuid
    assert meta["repo_path"] == str(git_repo.resolve())
    assert meta["remote_url"] is None
    assert meta["default_branch"] == "main"
    assert meta["last_synced_commit"] == git(git_repo, "rev-parse", "HEAD")
    assert meta["last_consolidated_at"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", meta["created_at"])

    assert ".hippo" in (git_repo / ".gitignore").read_text().splitlines()
    registry = json.loads((root / "projects.json").read_text())
    assert registry == [{"uuid": uuid, "repo_path": str(git_repo.resolve())}]
    assert "hippo open" in capsys.readouterr().out.split(NEXT_STEPS_HEADING)[1]


def test_link_from_subfolder(installed, git_repo, monkeypatch):
    sub = git_repo / "src" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert link() == 0
    assert (git_repo / ".hippo").exists()
    assert not (sub / ".hippo").exists()


def test_empty_repo_links_without_start_commit(installed, tmp_path, monkeypatch, root):
    repo = tmp_path / "empty"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    monkeypatch.chdir(repo)
    assert link() == 0
    meta = json.loads((root / "projects" / dot_uuid(repo) / "meta.json").read_text())
    assert meta["last_synced_commit"] is None
    assert meta["default_branch"] == "main"


# --- .gitignore -------------------------------------------------------------

def test_gitignore_appended_without_trailing_newline(installed, git_repo, monkeypatch):
    (git_repo / ".gitignore").write_text("node_modules")
    monkeypatch.chdir(git_repo)
    link()
    assert (git_repo / ".gitignore").read_text() == "node_modules\n.hippo\n.claude/settings.local.json\n"


def test_gitignore_left_alone_when_already_ignored(installed, git_repo, monkeypatch):
    (git_repo / ".git" / "info" / "exclude").write_text(".hippo\n.claude/settings.local.json\n")
    monkeypatch.chdir(git_repo)
    link()
    assert not (git_repo / ".gitignore").exists()


# --- default branch ---------------------------------------------------------

def test_default_branch_from_origin_head(installed, tmp_path, monkeypatch, root):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "trunk")
    git(upstream, "commit", "-q", "--allow-empty", "-m", "init")
    repo = clone(upstream, tmp_path / "work")
    git(repo, "checkout", "-q", "-b", "feature")
    monkeypatch.chdir(repo)
    link()
    meta = json.loads((root / "projects" / dot_uuid(repo) / "meta.json").read_text())
    assert meta["default_branch"] == "trunk"
    assert meta["remote_url"] == str(upstream)


@pytest.mark.parametrize("branch,expected", [("master", "master"), ("dev", "dev")])
def test_default_branch_fallbacks(installed, tmp_path, monkeypatch, root, branch, expected):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", branch)
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    monkeypatch.chdir(repo)
    link()
    meta = json.loads((root / "projects" / dot_uuid(repo) / "meta.json").read_text())
    assert meta["default_branch"] == expected


@pytest.mark.parametrize("url", [
    "git@github.com:Me/App.git",
    "https://github.com/me/app",
    "https://github.com/me/app.git/",
    "ssh://git@github.com/me/app.git",
])
def test_normalize_remote(url):
    assert hgit.normalize_remote(url) == "github.com/me/app"


def test_normalize_remote_keeps_port():
    assert hgit.normalize_remote("ssh://git@host:2222/me/app.git") == "host:2222/me/app"


# --- re-link ----------------------------------------------------------------

@pytest.fixture
def recloned(installed, git_repo, tmp_path, monkeypatch, capsys):
    """Link clone A, delete it, and return (old uuid, fresh clone B) in B's folder."""
    a = clone(git_repo, tmp_path / "a")
    monkeypatch.chdir(a)
    link()
    old = dot_uuid(a)
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(a)
    b = clone(git_repo, tmp_path / "b")
    monkeypatch.chdir(b)
    capsys.readouterr()
    return old, b


def test_reclone_offers_reuse(recloned, monkeypatch, root):
    old, b = recloned
    terminal(monkeypatch, "")
    assert link() == 0
    assert dot_uuid(b) == old
    meta = json.loads((root / "projects" / old / "meta.json").read_text())
    assert meta["repo_path"] == str(b.resolve())
    assert json.loads((root / "projects.json").read_text()) == [
        {"uuid": old, "repo_path": str(b.resolve())}
    ]


def test_reclone_declined_creates_new(recloned, monkeypatch):
    old, b = recloned
    terminal(monkeypatch, "n")
    assert link() == 0
    assert dot_uuid(b) != old


def test_reclone_without_terminal_asks_for_flag(recloned, monkeypatch, capsys):
    old, b = recloned
    monkeypatch.setattr(deps, "is_interactive", lambda: False)
    assert link() == 1
    steps = capsys.readouterr().err.split(NEXT_STEPS_HEADING)[1]
    assert f"hippo link --reuse {old}" in steps and "hippo link --new" in steps
    assert not (b / ".hippo").exists()


def test_reuse_flag(recloned):
    old, b = recloned
    assert link("--reuse", old) == 0
    assert dot_uuid(b) == old


def test_new_flag(recloned):
    old, b = recloned
    assert link("--new") == 0
    assert dot_uuid(b) != old


def test_reuse_unknown_uuid(installed, git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert link("--reuse", "nope") == 1
    assert "no Hippo memory with uuid nope" in capsys.readouterr().err


def test_live_clone_not_offered(installed, git_repo, tmp_path, monkeypatch):
    a = clone(git_repo, tmp_path / "a")
    monkeypatch.chdir(a)
    link()
    b = clone(git_repo, tmp_path / "b")
    monkeypatch.chdir(b)
    monkeypatch.setattr("builtins.input", lambda _p: pytest.fail("prompted"))
    assert link() == 0
    assert dot_uuid(b) != dot_uuid(a)


def test_relink_after_manual_delete_replaces_stale_entry(installed, git_repo, monkeypatch, root):
    monkeypatch.chdir(git_repo)
    link()
    old = dot_uuid(git_repo)
    # The user deletes the memory folder and .hippo by hand.
    shutil.rmtree(root / "projects" / old)
    (git_repo / ".hippo").unlink()
    assert link() == 0
    new = dot_uuid(git_repo)
    assert new != old
    assert json.loads((root / "projects.json").read_text()) == [
        {"uuid": new, "repo_path": str(git_repo.resolve())}
    ]
