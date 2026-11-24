import json
import re
import shutil
import textwrap

import pytest

from hippo import open_memory
from hippo.cli import main
from hippo.ui import NEXT_STEPS_HEADING


@pytest.fixture
def linked(fake_claude, git_repo, monkeypatch, capsys, home):
    assert main(["install"]) == 0
    monkeypatch.chdir(git_repo)
    assert main(["link", "--no-init"]) == 0
    capsys.readouterr()
    uuid = json.loads((git_repo / ".hippo").read_text())["uuid"]
    return home / "agent-memory" / "projects" / uuid


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())


@pytest.fixture
def populated(linked):
    write(linked / "features/auth/feature.json", json.dumps({
        "slug": "auth", "name": "Auth",
        "summary": "Lets users sign in and stay signed in.",
        "keywords": ["login", "sessions", "key rotation"],
        "key_files": [
            {"path": "src/auth/login.ts", "role": "login handler, issues session token"},
            {"path": "src/auth/oauth/**", "role": "all OAuth providers"},
        ],
    }))
    write(linked / "features/billing/feature.json", json.dumps({
        "slug": "billing", "name": "Billing", "summary": "Charges users.",
        "keywords": ["plans", "invoices", "Stripe webhooks"], "key_files": [],
    }))
    write(linked / "features/auth/overview.md", """
        # Auth
        Lets users sign in with email and stay signed in for 7 days.
        """)
    write(linked / "features/auth/technical.md", """
        # Auth: technical
        Sessions live in the sessions table; billing reads users.plan_id.
        """)
    write(linked / "features/auth/decisions.json", json.dumps([
        {"id": "auth-001", "date": "2026-09-24", "status": "active", "superseded_by": None,
         "decision": "Opaque session IDs, not JWT.", "why": "Instant revocation.",
         "rejected": ["JWT in localStorage: XSS risk"], "also_affects": ["billing"],
         "source": "patches/2026-09-24T143012.md"},
        {"id": "auth-000", "date": "2026-09-01", "status": "superseded",
         "superseded_by": "auth-001", "decision": "24h sessions."},
    ]))
    write(linked / "patches/2026-10-03T143012.md", """
        ---
        author: me
        created_at: 2026-10-03T14:30:12
        commits: [a1b2c3d]
        features: [auth]
        ---
        ## Changed
        x
        """)
    write(linked / "needs-review.json", json.dumps([{"id": "r-001", "created": "2026-10-03", "kind": "agent", "detail": "Is src/orphan.ts still used?", "status": "open"}, {"id": "r-002", "kind": "agent", "detail": "old", "status": "dismissed"}]))
    return linked


def out(capsys, *args):
    assert main(["open", *args]) == 0
    return capsys.readouterr().out


def test_summary_before_init(linked, capsys):
    text = out(capsys)
    assert "none yet" in text
    assert "Unmerged patches   0" in text
    assert "Last consolidated  never" in text
    assert f"~/agent-memory/projects/{linked.name}/" in text


def test_summary_lists_features_with_keywords(populated, capsys):
    text = out(capsys)
    assert "Features (2)" in text
    assert "  auth      login, sessions, key rotation" in text
    assert "  billing   plans, invoices, Stripe webhooks" in text
    assert "Unmerged patches   1   latest 2026-10-03 14:30 (auth)" in text
    assert "Needs review       1 item" in text
    assert "hippo open --feature" in text.split(NEXT_STEPS_HEADING)[1]


def test_feature_view(populated, capsys):
    text = out(capsys, "--feature", "auth")
    assert text.startswith("auth · Auth\nLets users sign in and stay signed in.\n")
    assert "Keywords: login, sessions, key rotation" in text
    assert re.search(r"^  src/auth/login\.ts +login handler, issues session token$", text, re.M)
    assert re.search(r"^  src/auth/oauth/\*\* +all OAuth providers$", text, re.M)
    assert "\nOverview\n  Lets users sign in with email" in text
    assert "\nTechnical\n  Sessions live in the sessions table" in text
    assert "# Auth" not in text
    assert "2026-10-03 14:30  2026-10-03T143012.md" in text
    # Decisions stay hidden; only a pointer to them.
    assert "Opaque session IDs" not in text
    assert "hippo open --feature auth --decisions" in text


def test_decisions_view(populated, capsys):
    text = out(capsys, "--feature", "auth", "--decisions")
    assert text.startswith("auth · decisions (1 active, 1 superseded)")
    assert "auth-001 · 2026-09-24 · active\n  Opaque session IDs, not JWT." in text
    assert "Rejected: JWT in localStorage: XSS risk" in text
    assert "Also affects: billing" in text
    assert "auth-000 · 2026-09-01 · superseded by auth-001" in text


def test_decisions_view_empty(populated, capsys):
    text = out(capsys, "-f", "billing", "--decisions")
    assert "No decisions recorded yet" in text


def test_decisions_needs_feature(populated, capsys):
    assert main(["open", "--decisions"]) == 1
    assert "--decisions needs a feature" in capsys.readouterr().err


def test_unreadable_feature_json_flagged(populated, capsys):
    (populated / "features/auth/feature.json").write_text("{broken")
    text = out(capsys)
    assert "! auth/feature.json is unreadable" in text


def test_unknown_feature_suggests(populated, capsys):
    assert main(["open", "-f", "auht"]) == 1
    err = capsys.readouterr().err
    assert "no feature named 'auht'" in err
    assert "hippo open --feature auth" in err


def test_path_flag(linked, capsys):
    assert out(capsys, "--path").strip() == str(linked)


def test_not_linked(fake_claude, git_repo, monkeypatch, capsys):
    main(["install"])
    monkeypatch.chdir(git_repo)
    capsys.readouterr()
    assert main(["open"]) == 1
    assert "hippo link" in capsys.readouterr().err


def test_memory_missing(linked, capsys):
    shutil.rmtree(linked)
    assert main(["open"]) == 1
    assert "memory for this repo is missing" in capsys.readouterr().err


def test_finder(populated, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(open_memory, "reveal", calls.append)
    out(capsys, "--finder")
    out(capsys, "--feature", "auth", "--finder")
    assert calls == [populated, populated / "features" / "auth"]
