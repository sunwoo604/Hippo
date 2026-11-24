"""`hippo unlink [PROJECT]`: disconnect a repo from Hippo. Run by the user.

Nothing is deleted: the memory moves to ~/agent-memory/archived-projects/.
PROJECT (a repo folder name or uuid, as `hippo status` shows) works from
anywhere, so a repo that was moved or deleted can still be unlinked; without
it, the repo containing the current folder is unlinked.

Left in place on purpose: the repo's .gitignore lines (harmless), the global
setup and the daily job (it simply has one repo fewer).
"""

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from hippo import claude_settings, deps, git, paths, project, registry, ui
from hippo.errors import HippoError
from hippo.project import Project


def run(args: argparse.Namespace) -> int:
    if os.environ.get("HIPPO_AGENT"):
        raise HippoError("`hippo unlink` can't be run by Hippo's own agents.")
    uuid, repo = _target(args.project)
    p = Project(uuid, repo)
    name = repo.name
    linked_here = project.dot_uuid(repo) == uuid  # only touch the repo if it's linked to this memory
    dest = _archive_dest(name, uuid) if p.dir.is_dir() else None

    if not args.yes and not _confirmed(p, name, linked_here, dest):
        ui.say("skipped", "nothing changed")
        return 1

    from hippo.sync import Lock, log  # not while a sync is writing this memory
    with Lock():
        if linked_here:
            status, detail = claude_settings.remove(repo)
            ui.say(status, f"{ui.tilde(claude_settings.settings_file(repo))}: {detail}")
            project.dot_file(repo).unlink(missing_ok=True)
            ui.say("removed", ui.tilde(project.dot_file(repo)))
        if registry.remove(uuid):
            ui.say("removed", f"{name} from {ui.tilde(paths.registry_file())}")
        if dest:
            _mark_unlinked(p)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p.dir), str(dest))
            ui.say("archived", f"memory -> {ui.tilde(dest)}/")
        for leftover in [paths.root() / "state" / uuid, *(paths.root() / "tmp").glob(f"{uuid}-*")]:
            shutil.rmtree(leftover, ignore_errors=True)
        log(name, uuid, f"unlinked; memory archived to {dest}" if dest else "unlinked")

    steps = []
    if linked_here:
        steps.append(f"The .gitignore lines for {paths.DOT_FILE} and {claude_settings.SETTINGS} "
                     "were left in place; remove them if you like.")
        steps.append(f"To link it again later (starts fresh), run `hippo link` in {ui.tilde(repo)}.")
    ui.next_steps(steps)
    return 0


def _target(arg: str | None) -> tuple[str, Path]:
    """(uuid, repo path) of the project to unlink."""
    if arg is None:
        repo = git.toplevel(Path.cwd())
        uuid = project.dot_uuid(repo)
        if uuid is None:
            raise HippoError(f"this repo isn't linked to Hippo: {repo}",
                             next_steps=["Run `hippo status` to see the linked repos, then "
                                         "`hippo unlink <name>` from anywhere."])
        return uuid, repo

    entries = registry.load()
    by_path = Path(arg).expanduser()
    matches = [e for e in entries if e["uuid"] == arg] or [
        e for e in entries
        if Path(e["repo_path"]).name == arg
        or (len(arg) >= 4 and e["uuid"].startswith(arg))
        or (by_path.is_absolute() and Path(e["repo_path"]) == by_path)]
    if not matches:
        raise HippoError(f"no linked repo called '{arg}'.",
                         next_steps=["Run `hippo status` to see the linked repos and their uuids."])
    if len(matches) > 1:
        raise HippoError(f"more than one linked repo matches '{arg}'.",
                         next_steps=[f"Use the uuid instead:  hippo unlink {e['uuid']}   "
                                     f"({ui.tilde(Path(e['repo_path']))})" for e in matches])
    return matches[0]["uuid"], Path(matches[0]["repo_path"])


def _archive_dest(name: str, uuid: str) -> Path:
    base = paths.archived_projects_dir() / f"{name}-{datetime.now():%Y-%m-%d}-{uuid[:8]}"
    return base if not base.exists() else base.with_name(f"{base.name}-{datetime.now():%H%M%S}")


def _confirmed(p: Project, name: str, linked_here: bool, dest: Path | None) -> bool:
    lines = [f"Unlink {name} ({ui.tilde(p.repo)}{'' if p.repo.is_dir() else ', folder not found'})?"]
    if linked_here:
        lines.append(f"  - removes {paths.DOT_FILE} and Hippo's hooks from {claude_settings.SETTINGS}")
    if dest:
        lines.append(f"  - moves its memory to {ui.tilde(dest)}/ (nothing is deleted)")
        notes = len(list((p.dir / "patches").glob("*.md")))
        if notes:
            lines.append(f"  - {notes} patch note(s) not synced yet stay unsynced in the archive "
                         "(run `hippo sync` first to fold them into the docs)")
    if not deps.is_interactive():
        raise HippoError("\n".join(lines[:1]) + " Unlinking needs a confirmation.",
                         next_steps=[f"Run `hippo unlink {p.uuid} --yes` to skip the question."])
    print("\n".join(lines))
    return ui.confirm("Continue? [y/N] ", default=False)


def _mark_unlinked(p: Project) -> None:
    try:
        meta = p.meta()
    except (OSError, json.JSONDecodeError):
        return
    meta["unlinked_at"] = datetime.now().isoformat(timespec="seconds")
    p.save_meta(meta)


def add_parser(sub) -> None:
    p = sub.add_parser("unlink", help="disconnect a repo from Hippo and archive its memory")
    p.add_argument("project", nargs="?", metavar="PROJECT",
                   help="repo name or uuid (see `hippo status`); default: the current repo")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(func=run)
