import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FAKES = Path(__file__).parent / "fakes"
SRC = Path(__file__).parent.parent / "src"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets an empty HOME so nothing touches the real machine."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Subprocesses (`python -m hippo`) import from the checkout, not the install.
    monkeypatch.setenv("PYTHONPATH", str(SRC))
    # Keep git usable and deterministic under the fake HOME.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{var}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{var}_EMAIL", "test@example.com")
    # Never load real launchd jobs or show real notifications: stubs first on PATH.
    system = tmp_path / "fakesystem"
    system.mkdir()
    for tool in ("launchctl", "osascript"):
        shutil.copy(FAKES / tool, system / tool)
        (system / tool).chmod(0o755)
        monkeypatch.setenv(f"FAKE_{tool.upper()}_LOG", str(tmp_path / f"{tool}-calls.jsonl"))
    monkeypatch.setenv("PATH", f"{system}{os.pathsep}{os.environ['PATH']}")
    return home


class FakeClaude:
    def __init__(self, log: Path):
        self.log = log

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """Put a stub `claude` first on PATH and record every invocation."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    shutil.copy(FAKES / "claude", stub)
    stub.chmod(0o755)
    log = tmp_path / "claude-calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return FakeClaude(log)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path):
    """A repo on `main` with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# demo\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return repo
