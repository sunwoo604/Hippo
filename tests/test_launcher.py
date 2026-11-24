import json
import os
import subprocess

import pytest

from hippo.cli import main


@pytest.fixture
def root(home):
    return home / "agent-memory"


def run_launcher(launcher, *args, env_drop=()):
    env = {k: v for k, v in os.environ.items() if k not in env_drop}
    return subprocess.run(
        [str(launcher), *args], capture_output=True, text=True, env=env, timeout=30
    )


def test_cli_route_launcher(fake_claude, root, home):
    main(["install"])
    launcher = root / "bin" / "hippo"
    assert os.access(launcher, os.X_OK)
    assert "HIPPO_ROUTE=cli" in launcher.read_text()
    assert not (root / "lib").exists()

    link = home / ".local" / "bin" / "hippo"
    assert link.is_symlink() and link.resolve() == launcher.resolve()

    r = run_launcher(launcher, "--version")
    assert r.returncode == 0 and r.stdout.startswith("hippo ")


@pytest.fixture
def plugin_route(monkeypatch):
    monkeypatch.setenv("HIPPO_ROUTE", "plugin")


def test_plugin_route_runs_from_snapshot(fake_claude, plugin_route, root):
    main(["install"])
    assert json.loads((root / "config.json").read_text())["route"] == "plugin"
    assert (root / "lib" / "hippo" / "__init__.py").exists()
    assert not list((root / "lib").rglob("__pycache__"))

    launcher = root / "bin" / "hippo"
    assert "HIPPO_ROUTE=plugin" in launcher.read_text()
    # Without the checkout on PYTHONPATH, only the snapshot can satisfy the import.
    r = run_launcher(launcher, "--version", env_drop=("PYTHONPATH",))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("hippo ")


def test_plugin_route_reinstall_is_quiet(fake_claude, plugin_route, capsys):
    main(["install"])
    capsys.readouterr()
    main(["install"])
    out = capsys.readouterr().out
    assert "created" not in out and "updated" not in out


def test_stale_snapshot_refreshed(fake_claude, plugin_route, root, capsys):
    main(["install"])
    (root / "lib" / "hippo" / "cli.py").write_text("# stale\n")
    capsys.readouterr()
    main(["install"])
    assert "# stale" not in (root / "lib" / "hippo" / "cli.py").read_text()
    assert "updated" in capsys.readouterr().out


def test_route_switch_reported(fake_claude, monkeypatch, root, capsys):
    main(["install"])
    monkeypatch.setenv("HIPPO_ROUTE", "plugin")
    capsys.readouterr()
    main(["install"])
    assert "install route: cli -> plugin" in capsys.readouterr().out
    assert "HIPPO_ROUTE=plugin" in (root / "bin" / "hippo").read_text()


def test_next_step_uses_full_path_when_local_bin_not_on_path(fake_claude, home, capsys):
    main(["install"])
    out = capsys.readouterr().out
    # The test PATH never contains the fake HOME's ~/.local/bin.
    assert "~/agent-memory/bin/hippo link" in out
