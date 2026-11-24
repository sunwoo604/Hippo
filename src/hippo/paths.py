"""Filesystem locations used by Hippo.

Everything is derived from the user's home directory at call time, so tests
can redirect the whole tool by setting HOME.
"""

from pathlib import Path

DOT_FILE = ".hippo"


def root() -> Path:
    return Path.home() / "agent-memory"


def legacy_prompts_dir() -> Path:
    """Where versions before 2026-10-06 copied the prompts. No longer read."""
    return root() / "prompts"


def bin_dir() -> Path:
    return root() / "bin"


def lib_dir() -> Path:
    """Snapshot of the engine for the plugin route (the plugin's own dir moves on update)."""
    return root() / "lib"


def backups_dir() -> Path:
    return root() / "backups"


def logs_dir() -> Path:
    return root() / "logs"


def projects_dir() -> Path:
    return root() / "projects"


def archived_projects_dir() -> Path:
    return root() / "archived-projects"


def registry_file() -> Path:
    return root() / "projects.json"


def config_file() -> Path:
    return root() / "config.json"


def lock_file() -> Path:
    return root() / ".lock"


def project_dir(uuid: str) -> Path:
    return projects_dir() / uuid


def launcher() -> Path:
    """Stable `hippo` entry point for the terminal and the daily job."""
    return bin_dir() / "hippo"


def local_bin_link() -> Path:
    return Path.home() / ".local" / "bin" / "hippo"
