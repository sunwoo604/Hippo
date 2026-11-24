"""projects.json: the list of linked projects the daily job loops over."""

import json

from hippo import paths
from hippo.fsutil import atomic_write_text


def load() -> list[dict]:
    f = paths.registry_file()
    return json.loads(f.read_text()) if f.exists() else []


def save(entries: list[dict]) -> None:
    atomic_write_text(paths.registry_file(), json.dumps(entries, indent=2) + "\n")


def upsert(uuid: str, repo_path: str) -> None:
    """Register a project, replacing any entry for the same uuid or the same repo
    (e.g. one left behind when its memory or `.hippo` was deleted by hand)."""
    entries = [e for e in load() if e["uuid"] != uuid and e["repo_path"] != repo_path]
    entries.append({"uuid": uuid, "repo_path": repo_path})
    save(entries)


def remove(uuid: str) -> bool:
    entries = load()
    kept = [e for e in entries if e["uuid"] != uuid]
    save(kept)
    return len(kept) != len(entries)
