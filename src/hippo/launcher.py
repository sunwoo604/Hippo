"""The stable `hippo` command at ~/agent-memory/bin/hippo.

The terminal (via ~/.local/bin/hippo) and the daily job always call this
launcher, whichever route installed Hippo:

- cli route (pipx / venv): exec this install's Python, which already has hippo.
- plugin route: the plugin's directory moves on every update, so the engine
  is copied to ~/agent-memory/lib/ and the launcher runs that copy.
"""

import os
import shlex
import shutil
import sys
from pathlib import Path

import hippo
from hippo import paths, ui
from hippo.fsutil import atomic_write_text


def install(route: str) -> list[tuple[str, Path | str]]:
    """Write the launcher (and snapshot for the plugin route). Returns (status, what) lines."""
    lines = []
    env = [f"HIPPO_ROUTE={route}"]
    if route == "plugin":
        lines.append((_snapshot_engine(), paths.lib_dir() / "hippo"))
        env.append(f"PYTHONPATH={shlex.quote(str(paths.lib_dir()))}")

    script = (
        "#!/bin/sh\n"
        f"# Written by `hippo install` (route: {route}). Re-run it to refresh.\n"
        f"{' '.join(env)} exec {shlex.quote(sys.executable)} -m hippo \"$@\"\n"
    )
    target = paths.launcher()
    status = "unchanged" if target.exists() and target.read_text() == script else (
        "updated" if target.exists() else "created"
    )
    if status != "unchanged":
        atomic_write_text(target, script)
        target.chmod(0o755)
    lines.append((status, target))
    lines.append(_link_local_bin(target))
    return lines


def _snapshot_engine() -> str:
    """Copy the running hippo package into lib/, replacing any older copy."""
    src = Path(hippo.__file__).parent.resolve()
    dest = paths.lib_dir() / "hippo"
    if dest.exists() and (dest.resolve() == src or _tree(dest) == _tree(src)):
        return "unchanged"
    staging = paths.lib_dir() / "hippo.new"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(src, staging, ignore=shutil.ignore_patterns("__pycache__"))
    existed = dest.exists()
    shutil.rmtree(dest, ignore_errors=True)
    staging.rename(dest)
    return "updated" if existed else "created"


def _tree(d: Path) -> dict[str, bytes]:
    return {
        str(f.relative_to(d)): f.read_bytes()
        for f in d.rglob("*")
        if f.is_file() and "__pycache__" not in f.parts
    }


def _link_local_bin(target: Path) -> tuple[str, Path | str]:
    """Expose the launcher as plain `hippo` in the user's terminal."""
    link = paths.local_bin_link()
    if link.is_symlink() or link.exists():
        if link.resolve() == target.resolve():
            return ("ok", link)
        return ("skipped", f"{ui.tilde(link)} already exists and points elsewhere; left alone")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return ("created", f"{ui.tilde(link)} -> {ui.tilde(target)}")


def on_path() -> bool:
    return str(paths.local_bin_link().parent) in os.environ.get("PATH", "").split(os.pathsep)
