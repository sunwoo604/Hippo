import json

import pytest

from hippo.cli import main


@pytest.fixture
def mem(fake_claude, git_repo, monkeypatch, capsys, home):
    (git_repo / "src").mkdir()
    (git_repo / "src/a.ts").write_text("x\n")
    assert main(["install"]) == 0
    monkeypatch.chdir(git_repo)
    assert main(["link", "--no-init"]) == 0
    for slug in ("auth", "billing"):
        main(["feature", "set", slug, "--name", slug, "--summary", "S.", "--keyword", "a",
              "--keyword", "b", "--keyword", "c", "--key-file", "src/a.ts", "x"])
    uuid = json.loads((git_repo / ".hippo").read_text())["uuid"]
    d = home / "agent-memory" / "projects" / uuid
    (d / "patches" / "2026-10-20T101010.md").write_text(
        "---\ncreated_at: 2026-10-20T10:10:10\nfeatures: [auth]\n---\n## Decisions\n- [auth] x\n")
    monkeypatch.setenv("HIPPO_AGENT", "1")
    monkeypatch.chdir(d)
    capsys.readouterr()
    return d


def add(*extra, decision="Opaque session IDs.", why="Instant revocation."):
    return main(["decision", "add", "auth", "--decision", decision, "--why", why,
                 "--source", "patches/2026-10-20T101010.md", *extra])


def decisions(mem, slug="auth"):
    return json.loads((mem / "features" / slug / "decisions.json").read_text())


def test_add(mem, capsys):
    assert add("--rejected", "JWT: can't revoke", "--also-affects", "billing") == 0
    [d] = decisions(mem)
    assert d == {"id": "auth-001", "date": "2026-10-20", "status": "active", "superseded_by": None,
                 "decision": "Opaque session IDs.", "why": "Instant revocation.",
                 "rejected": ["JWT: can't revoke"], "also_affects": ["billing"],
                 "source": "patches/2026-10-20T101010.md"}
    assert "created   decision auth-001" in capsys.readouterr().out


def test_ids_increment(mem):
    add()
    add(decision="Sliding expiry.")
    assert [d["id"] for d in decisions(mem)] == ["auth-001", "auth-002"]


def test_same_decision_twice_refused(mem, capsys):
    add()
    assert add() == 1
    assert "already recorded as auth-001" in capsys.readouterr().err


@pytest.mark.parametrize("args,problem", [
    (["decision", "add", "nope", "--decision", "d", "--why", "w", "--source", "patches/2026-10-20T101010.md"],
     "feature 'nope' doesn't exist"),
    (["decision", "add", "auth", "--decision", "d", "--source", "patches/2026-10-20T101010.md"],
     "--why is required"),
    (["decision", "add", "auth", "--decision", "a\nb", "--why", "w", "--source", "patches/2026-10-20T101010.md"],
     "--decision must be one line"),
    (["decision", "add", "auth", "--decision", "d", "--why", "w", "--source", "patches/missing.md"],
     "--source must be a patch note"),
    (["decision", "add", "auth", "--decision", "d", "--why", "w", "--source", "patches/2026-10-20T101010.md",
      "--also-affects", "nope"], "--also-affects 'nope' isn't a feature"),
])
def test_add_validation(mem, capsys, args, problem):
    assert main(args) == 1
    assert problem in capsys.readouterr().err
    assert not (mem / "features/auth/decisions.json").exists()


def test_only_hippo_agents_record_decisions(mem, monkeypatch, capsys):
    monkeypatch.delenv("HIPPO_AGENT")
    assert add() == 1
    assert "recorded by Hippo's consolidation" in capsys.readouterr().err


def test_supersede(mem):
    add()
    add(decision="Sliding expiry.")
    assert main(["decision", "supersede", "auth-001", "--by", "auth-002"]) == 0
    old, new = decisions(mem)
    assert old["status"] == "superseded" and old["superseded_by"] == "auth-002"
    assert new["status"] == "active"


@pytest.mark.parametrize("old,by,problem", [
    ("auth-009", "auth-001", "no decision 'auth-009'"),
    ("auth-001", "auth-009", "no decision 'auth-009'"),
    ("auth-001", "auth-001", "can't supersede itself"),
])
def test_supersede_errors(mem, capsys, old, by, problem):
    add()
    assert main(["decision", "supersede", old, "--by", by]) == 1
    assert problem in capsys.readouterr().err


def test_supersede_twice_refused(mem, capsys):
    add()
    add(decision="B.")
    add(decision="C.")
    main(["decision", "supersede", "auth-001", "--by", "auth-002"])
    assert main(["decision", "supersede", "auth-001", "--by", "auth-003"]) == 1
    assert "already superseded" in capsys.readouterr().err
