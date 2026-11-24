"""The repo doubles as a Claude Code plugin and its own marketplace."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import hippo

REPO = Path(__file__).parent.parent
PLUGIN_BIN = REPO / "bin" / "hippo"


def manifest(name):
    return json.loads((REPO / ".claude-plugin" / name).read_text())


def test_versions_agree():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert manifest("plugin.json")["version"] == hippo.__version__
    assert pyproject["project"]["version"] == hippo.__version__


def test_marketplace_points_at_repo_root():
    [entry] = manifest("marketplace.json")["plugins"]
    assert entry["name"] == manifest("plugin.json")["name"] == "hippo"
    assert entry["source"] == "."


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(extra)
    return env


def test_plugin_bin_runs_bundled_engine():
    assert os.access(PLUGIN_BIN, os.X_OK)
    r = subprocess.run([str(PLUGIN_BIN), "--version"], capture_output=True, text=True,
                       env=_env(), timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"hippo {hippo.__version__}"


def test_plugin_bin_install_records_plugin_route(fake_claude, home):
    r = subprocess.run([str(PLUGIN_BIN), "install"], capture_output=True, text=True,
                       env=_env(), stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr
    root = home / "agent-memory"
    assert json.loads((root / "config.json").read_text())["route"] == "plugin"
    assert (root / "lib" / "hippo" / "cli.py").exists()


def test_plugin_bin_explains_missing_python(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in ("python3.14", "python3.13", "python3.12", "python3"):
        stub = stubs / name
        stub.write_text("#!/bin/sh\nexit 1\n")  # pretends to be too old
        stub.chmod(0o755)
    r = subprocess.run([str(PLUGIN_BIN), "--version"], capture_output=True, text=True,
                       env=_env(PATH=f"{stubs}:/usr/bin:/bin"), timeout=30)
    assert r.returncode == 1
    assert "needs Python 3.12" in r.stderr
    assert "Next steps for you" in r.stderr
