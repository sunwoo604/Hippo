import json

import pytest

from hippo.cli import main
from hippo.features import INDEX_NOTE


@pytest.fixture
def linked(fake_claude, git_repo, monkeypatch, capsys, home):
    (git_repo / "src" / "auth" / "oauth").mkdir(parents=True)
    for f in ("src/auth/login.ts", "src/auth/oauth/google.ts", "db/001_users.sql"):
        (git_repo / f).parent.mkdir(parents=True, exist_ok=True)
        (git_repo / f).write_text("x\n")
    assert main(["install"]) == 0
    monkeypatch.chdir(git_repo)
    assert main(["link", "--no-init"]) == 0
    capsys.readouterr()
    uuid = json.loads((git_repo / ".hippo").read_text())["uuid"]
    return home / "agent-memory" / "projects" / uuid


AUTH = [
    "feature", "set", "auth", "--name", "Auth",
    "--summary", "Lets users sign in and stay signed in.",
    "--keyword", "login", "--keyword", "sessions", "--keyword", "key rotation",
    "--key-file", "src/auth/login.ts", "login handler",
    "--key-file", "src/auth/oauth/**", "all OAuth providers",
    "--key-file", "db/001_users.sql", "users table",
]


def test_set_writes_json_and_index(linked, capsys):
    assert main(AUTH) == 0
    data = json.loads((linked / "features/auth/feature.json").read_text())
    assert data == {
        "slug": "auth", "name": "Auth",
        "summary": "Lets users sign in and stay signed in.",
        "keywords": ["login", "sessions", "key rotation"],
        "key_files": [
            {"path": "src/auth/login.ts", "role": "login handler"},
            {"path": "src/auth/oauth/**", "role": "all OAuth providers"},
            {"path": "db/001_users.sql", "role": "users table"},
        ],
    }
    index = (linked / "index.md").read_text()
    assert INDEX_NOTE in index
    assert ("- auth (Auth): Lets users sign in and stay signed in. "
            "Keywords: login, sessions, key rotation. → features/auth/") in index
    assert "created" in capsys.readouterr().out


def test_set_replaces_and_reports_update(linked, capsys):
    main(AUTH)
    capsys.readouterr()
    changed = AUTH[:5] + ["--summary", "New summary."] + AUTH[7:]
    assert main(changed) == 0
    assert json.loads((linked / "features/auth/feature.json").read_text())["summary"] == "New summary."
    assert "updated" in capsys.readouterr().out


def test_index_lists_all_features_sorted(linked):
    main(AUTH)
    main(["feature", "set", "billing", "--name", "Billing", "--summary", "Charges users",
          "--keyword", "plans", "--keyword", "invoices", "--keyword", "stripe",
          "--key-file", "db/001_users.sql", "plan column"])
    lines = [l for l in (linked / "index.md").read_text().splitlines() if l.startswith("- ")]
    assert [l.split()[1] for l in lines] == ["auth", "billing"]


@pytest.mark.parametrize("args,problem", [
    (["Auth_Bad"], "lowercase kebab-case"),
    (["auth", "--keyword", "a", "--keyword", "b"], "give 3-6 --keyword"),
    (["auth", "--keyword", "a", "--keyword", "A", "--keyword", "b"], "distinct"),
    (["auth", "--key-file", "src/nope.ts", "x"], "does not exist"),
    (["auth", "--key-file", "lib/**", "x"], "matches no files"),
    (["auth", "--key-file", "/etc/passwd", "x"], "relative to the repo root"),
    (["auth", "--key-file", "../x", "x"], "relative to the repo root"),
    (["auth", "--key-file", "src/auth/login.ts", ""], "needs a one-line role"),
    (["auth", "--summary", "two\nlines"], "one line"),
])
def test_set_rejects_bad_input(linked, capsys, args, problem):
    base = {"--name": "Auth", "--summary": "S."}
    argv = ["feature", "set", *args]
    for flag, value in base.items():
        if flag not in argv:
            argv += [flag, value]
    if "--keyword" not in argv:
        argv += ["--keyword", "a", "--keyword", "b", "--keyword", "c"]
    if "--key-file" not in argv:
        argv += ["--key-file", "src/auth/login.ts", "role"]
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "was not saved" in err and problem in err
    assert not list((linked / "features").glob("*/feature.json"))


def test_all_problems_reported_at_once(linked, capsys):
    assert main(["feature", "set", "auth"]) == 1
    err = capsys.readouterr().err
    for problem in ("--name is required", "--summary is required", "--keyword", "--key-file"):
        assert problem in err


def test_works_from_memory_dir(linked, monkeypatch):
    # Hippo's own agents run with the memory dir as their working directory.
    monkeypatch.chdir(linked)
    assert main(AUTH) == 0
    assert (linked / "features/auth/feature.json").exists()


def test_outside_project_fails(fake_claude, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(AUTH) == 1
    assert "not inside a git repository" in capsys.readouterr().err
