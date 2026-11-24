import json
import shlex

import pytest

from hippo import deps
from hippo.cli import main
from hippo.ui import NEXT_STEPS_HEADING


@pytest.fixture
def repo(git_repo):
    for f in ("src/auth/login.ts", "src/auth/session.ts", ".env"):
        (git_repo / f).parent.mkdir(parents=True, exist_ok=True)
        (git_repo / f).write_text("x\n")
    return git_repo


@pytest.fixture
def installed(fake_claude, capsys):
    assert main(["install"]) == 0
    capsys.readouterr()
    return fake_claude


@pytest.fixture
def agent_does(tmp_path, monkeypatch, home):
    """Script what the fake agent does during its run."""
    def script(*actions):
        f = tmp_path / "actions.json"
        f.write_text(json.dumps(list(actions)))
        monkeypatch.setenv("FAKE_CLAUDE_ACTIONS", str(f))
    return script


def feature_set(home, slug="auth"):
    launcher = home / "agent-memory" / "bin" / "hippo"
    return {"bash": shlex.join([
        str(launcher), "feature", "set", slug, "--name", "Auth",
        "--summary", "Lets users sign in.",
        "--keyword", "login", "--keyword", "sessions", "--keyword", "tokens",
        "--key-file", "src/auth/login.ts", "login handler",
        "--key-file", "src/auth/session.ts", "session middleware",
    ])}


def docs(slug="auth"):
    return [{"write": f"features/{slug}/overview.md", "content": "# Auth\nSign in.\n"},
            {"write": f"features/{slug}/technical.md", "content": "# Auth\nSessions table.\n"}]


def memory(repo, home):
    uuid = json.loads((repo / ".hippo").read_text())["uuid"]
    return home / "agent-memory" / "projects" / uuid


def agent_calls(fake):
    return [c for c in fake.calls() if "-p" in c["argv"]]


def test_link_runs_init(installed, repo, monkeypatch, home, agent_does, capsys):
    agent_does(feature_set(home), *docs())
    monkeypatch.chdir(repo)
    assert main(["link"]) == 0
    mem = memory(repo, home)
    assert (mem / "features/auth/feature.json").exists()
    assert "- auth (Auth): Lets users sign in." in (mem / "index.md").read_text()
    out = capsys.readouterr().out
    assert "save    feature auth" in out
    assert "write   features/auth/overview.md" in out
    assert "created   1 features (4 turns, ~$0.01 API-equivalent)" in out
    assert "hippo open" in out.split(NEXT_STEPS_HEADING)[1]


def test_agent_sandbox(installed, repo, monkeypatch, home, agent_does):
    agent_does(feature_set(home), *docs())
    monkeypatch.chdir(repo)
    main(["link"])
    [call] = agent_calls(installed)
    mem, real_repo = memory(repo, home), repo.resolve()
    argv, rules = call["argv"], call["settings"]["permissions"]

    assert call["cwd"] == str(mem)
    assert call["hippo_agent"] == "1"
    assert "--restricted" in argv
    assert argv[argv.index("--add-dir") + 1] == str(real_repo)
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep,Write,Edit,Bash"
    assert rules["defaultMode"] == "dontAsk"
    assert f"Edit(/{mem}/**)" in rules["allow"]
    assert f"Bash({home}/agent-memory/bin/hippo feature set *)" in rules["allow"]
    assert not any(r.startswith("Bash") and r != f"Bash({home}/agent-memory/bin/hippo feature set *)"
                   for r in rules["allow"])
    for rule in (f"Read(/{real_repo}/**/.env*)", f"Edit(/{mem}/index.md)",
                 f"Edit(/{mem}/features/*/feature.json)", f"Edit(/{real_repo}/**)"):
        assert rule in rules["deny"]
    assert str(home / ".claude" / "CLAUDE.md") in call["settings"]["claudeMdExcludes"]
    [hook] = call["settings"]["hooks"]["PreToolUse"]
    assert hook["matcher"] == "Bash"
    guard = hook["hooks"][0]["command"]
    assert guard.startswith(f"{home}/agent-memory/bin/hippo _guard-bash --allow ")
    assert "feature set" in guard

    prompt = call["stdin"]
    assert str(real_repo) in prompt and str(mem) in prompt
    assert f"{home}/agent-memory/bin/hippo feature set" in prompt
    assert "{{" not in prompt


def test_agent_failure_keeps_link(installed, repo, monkeypatch, home, agent_does, capsys):
    agent_does(feature_set(home))
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    monkeypatch.chdir(repo)
    assert main(["link"]) == 1
    assert (repo / ".hippo").exists()
    out = capsys.readouterr().out
    assert "failed    init agent" in out
    assert "hippo link --reinit" in out.split(NEXT_STEPS_HEADING)[1]


def test_no_features_is_failure(installed, repo, monkeypatch, agent_does, capsys):
    agent_does()
    monkeypatch.chdir(repo)
    assert main(["link"]) == 1
    assert "recorded no features" in capsys.readouterr().out


def test_missing_docs_reported(installed, repo, monkeypatch, home, agent_does, capsys):
    agent_does(feature_set(home))
    monkeypatch.chdir(repo)
    assert main(["link"]) == 1
    out = capsys.readouterr().out
    assert "! missing features/auth/overview.md" in out
    assert "! missing features/auth/technical.md" in out


def test_login_checked_before_any_writes(installed, repo, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CLAUDE_LOGGED_IN", "0")
    monkeypatch.setattr(deps, "is_interactive", lambda: False)
    monkeypatch.chdir(repo)
    assert main(["link"]) == 1
    assert not (repo / ".hippo").exists()


def test_refused_inside_hippo_agent(installed, repo, monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_AGENT", "1")
    monkeypatch.chdir(repo)
    assert main(["link"]) == 1
    assert "Hippo's own agents" in capsys.readouterr().err


# --- re-init ----------------------------------------------------------------

@pytest.fixture
def initialised(installed, repo, monkeypatch, home, agent_does, capsys):
    agent_does(feature_set(home), *docs())
    monkeypatch.chdir(repo)
    assert main(["link"]) == 0
    mem = memory(repo, home)
    (mem / "features/auth/decisions.json").write_text('[{"id": "auth-001"}]')
    capsys.readouterr()
    return mem


def test_reinit_backs_up_and_keeps_decisions(initialised, home, agent_does, monkeypatch, capsys):
    monkeypatch.setattr(deps, "is_interactive", lambda: False)
    agent_does(feature_set(home, "login"), *docs("login"))
    assert main(["link", "--reinit"]) == 0
    mem = initialised
    [backup] = (mem / "archive").glob("features-before-reinit-*")
    assert (backup / "auth/feature.json").exists()
    assert not (mem / "features/auth/feature.json").exists()
    assert (mem / "features/auth/decisions.json").read_text() == '[{"id": "auth-001"}]'
    assert (mem / "features/login/feature.json").exists()
    assert "- login" in (mem / "index.md").read_text() and "- auth" not in (mem / "index.md").read_text()


def test_reinit_declined_changes_nothing(initialised, monkeypatch):
    monkeypatch.setattr(deps, "is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _p: "n")
    assert main(["link", "--reinit"]) == 1
    assert (initialised / "features/auth/feature.json").exists()
    assert not list((initialised / "archive").glob("features-before-reinit-*"))


def test_reinit_requires_link(installed, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert main(["link", "--reinit"]) == 1
    assert "isn't linked" in capsys.readouterr().err


def test_prompt_comes_from_package_not_home(installed, repo, monkeypatch, home, agent_does):
    # A stale copy where old versions seeded prompts must be ignored.
    (home / "agent-memory" / "prompts").mkdir()
    (home / "agent-memory" / "prompts" / "init.md").write_text("STALE PROMPT\n")
    agent_does(feature_set(home), *docs())
    monkeypatch.chdir(repo)
    main(["link"])
    [call] = agent_calls(installed)
    assert "STALE PROMPT" not in call["stdin"]
    assert "Hippo init agent" in call["stdin"]
