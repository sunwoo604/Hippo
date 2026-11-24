"""One linked project: its `.hippo` dot file and its memory directory."""

import json
from dataclasses import dataclass
from pathlib import Path

from hippo import git, paths
from hippo.errors import HippoError
from hippo.fsutil import atomic_write_text

SUBDIRS = ("features", "patches", "archive")


@dataclass
class Project:
    uuid: str
    repo: Path

    @property
    def dir(self) -> Path:
        return paths.project_dir(self.uuid)

    @property
    def meta_file(self) -> Path:
        return self.dir / "meta.json"

    def meta(self) -> dict:
        return json.loads(self.meta_file.read_text())

    def save_meta(self, meta: dict) -> None:
        atomic_write_text(self.meta_file, json.dumps(meta, indent=2) + "\n")


def dot_file(repo: Path) -> Path:
    return repo / paths.DOT_FILE


def write_dot_file(repo: Path, uuid: str) -> None:
    atomic_write_text(dot_file(repo), json.dumps({"uuid": uuid}) + "\n")


def dot_uuid(repo: Path) -> str | None:
    """The uuid in the repo's `.hippo`, or None if it's missing or unreadable."""
    try:
        return json.loads(dot_file(repo).read_text())["uuid"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def current(cwd: Path) -> Project:
    """The linked project containing `cwd`, or a HippoError saying how to link it."""
    repo = git.toplevel(cwd)
    f = dot_file(repo)
    if not f.exists():
        raise HippoError(
            f"this repo isn't linked to Hippo: {repo}",
            next_steps=[f"Run `hippo link` inside {repo} to link it."],
        )
    try:
        uuid = json.loads(f.read_text())["uuid"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HippoError(f"{f} is not valid (expected {{\"uuid\": \"...\"}}).") from None
    project = Project(uuid, repo)
    if not project.dir.is_dir():
        raise HippoError(
            f"memory for this repo is missing: {project.dir}",
            next_steps=[f"Delete {f}, then run `hippo link` to start fresh."],
        )
    return project


def resolve(cwd: Path) -> Project:
    """The project for `cwd`: inside its memory dir (Hippo's own agents run
    there) or inside a linked repo (session agents)."""
    try:
        rel = cwd.resolve().relative_to(paths.projects_dir().resolve())
    except ValueError:
        return current(cwd)
    if not rel.parts:
        raise HippoError("run this inside a project's memory folder or a linked repo.")
    meta_file = paths.project_dir(rel.parts[0]) / "meta.json"
    if not meta_file.exists():
        raise HippoError(f"no meta.json for this memory folder: {meta_file.parent}")
    meta = json.loads(meta_file.read_text())
    return Project(meta["uuid"], Path(meta["repo_path"]))


def find_by_remote(remote: str) -> list[Project]:
    """Existing memory whose meta.json has the same remote (any URL spelling)."""
    wanted = git.normalize_remote(remote)
    found = []
    if not paths.projects_dir().is_dir():
        return found
    for meta_file in sorted(paths.projects_dir().glob("*/meta.json")):
        try:
            meta = json.loads(meta_file.read_text())
        except json.JSONDecodeError:
            continue
        if meta.get("remote_url") and git.normalize_remote(meta["remote_url"]) == wanted:
            found.append(Project(meta["uuid"], Path(meta["repo_path"])))
    return found
