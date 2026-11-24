"""`hippo link`: connect the current repo to Hippo memory. Run by the user."""

import argparse
import os
import uuid as uuidlib
from datetime import datetime
from pathlib import Path

from hippo import claude_settings, deps, git, init_agent, paths, project, registry, ui
from hippo.errors import HippoError
from hippo.project import Project


def run(args: argparse.Namespace) -> int:
    if os.environ.get("HIPPO_AGENT"):
        raise HippoError("`hippo link` can't be run by Hippo's own agents.")
    if not paths.config_file().exists():
        raise HippoError(
            "Hippo isn't set up on this machine yet.",
            next_steps=["Run `hippo install`.", "Then run `hippo link` again."],
        )
    repo = git.toplevel(Path.cwd())
    if args.reinit:
        return _reinit(project.current(repo))
    if project.dot_file(repo).exists():
        raise HippoError(
            f"already linked: {repo}",
            next_steps=["Run `hippo open` to see what Hippo knows about this repo.",
                        "To regenerate its feature docs, run:  hippo link --reinit"],
        )
    if not args.no_init:
        deps.require_claude_login()  # before any writes, so a failed login leaves nothing behind

    remote = git.remote_url(repo)
    branch = git.default_branch(repo)
    ui.say("ok", f"repo {ui.tilde(repo)} (default branch: {branch})")

    existing = _existing_memory(repo, remote, args)
    if existing:
        _reuse(existing, repo, remote, branch)
        ui.say("reused", f"memory {ui.tilde(existing.dir)}/")
        proj = existing
    else:
        proj = _create(repo, remote, branch)
        ui.say("created", f"memory {ui.tilde(proj.dir)}/")

    registry.upsert(proj.uuid, str(repo))
    ui.say("ok", f"registered in {ui.tilde(paths.registry_file())}")
    for path in (paths.DOT_FILE, str(claude_settings.SETTINGS)):
        ui.say(*_ignore(repo, path))
    status, detail = claude_settings.ensure(repo)
    ui.say(status, f"{claude_settings.SETTINGS}: {detail}")
    project.write_dot_file(repo, proj.uuid)  # last: its presence means "linked"
    ui.say("created", ui.tilde(project.dot_file(repo)))

    if existing or args.no_init:
        ui.next_steps(["Run `hippo open` to see what Hippo knows about this repo."])
        return 0
    return 0 if init_agent.run(proj) else 1


def _reinit(p: Project) -> int:
    deps.require_claude_login()
    has_docs = any((p.dir / "features").glob("*/feature.json"))
    if has_docs and deps.is_interactive() and not ui.confirm(
        "Re-init replaces this repo's feature docs (a backup goes to archive/; "
        "recorded decisions are kept). Continue? [Y/n] "
    ):
        return 1
    backup = init_agent.backup_existing(p)
    if backup:
        ui.say("saved", f"previous feature docs in {ui.tilde(backup)}/")
    return 0 if init_agent.run(p) else 1


def _existing_memory(repo: Path, remote: str | None, args) -> Project | None:
    """Memory to reuse: from --reuse, or offered when the same remote has orphaned memory."""
    if args.reuse:
        p = Project(args.reuse, repo)
        if not p.meta_file.exists():
            raise HippoError(f"no Hippo memory with uuid {args.reuse} in {paths.projects_dir()}")
        return p
    if args.new or not remote:
        return None

    # Memory whose repo is gone or no longer linked to it (e.g. the repo was re-cloned).
    orphans = [p for p in project.find_by_remote(remote) if not _still_linked(p)]
    if not orphans:
        return None
    if not deps.is_interactive():
        steps = [f"To reuse it, run:  hippo link --reuse {p.uuid}   (was at {p.repo})"
                 for p in orphans]
        raise HippoError(
            "found existing Hippo memory for this repo's remote; choose what to do.",
            next_steps=steps + ["To start fresh instead, run:  hippo link --new"],
        )
    for p in orphans:
        created = p.meta().get("created_at", "unknown date")
        if ui.confirm(f"Found Hippo memory for this remote (was at {p.repo}, "
                      f"created {created}). Reuse it? [Y/n] "):
            return p
    return None


def _still_linked(p: Project) -> bool:
    if not p.repo.is_dir():
        return False
    try:
        return project.current(p.repo).uuid == p.uuid
    except HippoError:
        return False


def _reuse(p: Project, repo: Path, remote: str | None, branch: str) -> None:
    for d in project.SUBDIRS:
        (p.dir / d).mkdir(parents=True, exist_ok=True)
    meta = p.meta()
    meta.update(repo_path=str(repo), remote_url=remote, default_branch=branch)
    p.save_meta(meta)


def _create(repo: Path, remote: str | None, branch: str) -> Project:
    p = Project(str(uuidlib.uuid4()), repo)
    for d in project.SUBDIRS:
        (p.dir / d).mkdir(parents=True)
    (p.dir / "needs-review.json").write_text("[]\n")
    p.save_meta({
        "uuid": p.uuid,
        "repo_path": str(repo),
        "remote_url": remote,
        "default_branch": branch,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        # Consolidation starts from here; None for a repo with no commits yet.
        "last_synced_commit": git.commit_of(repo, branch) or git.commit_of(repo, f"origin/{branch}"),
        "last_consolidated_at": None,
    })
    return p


def _ignore(repo: Path, path: str) -> tuple[str, str]:
    """Make sure git ignores `path` (relative to the repo root)."""
    if git.is_ignored(repo, path):
        return ("ok", f"{path} is gitignored")
    gitignore = repo / ".gitignore"
    text = gitignore.read_text() if gitignore.exists() else ""
    sep = "" if not text or text.endswith("\n") else "\n"
    gitignore.write_text(f"{text}{sep}{path}\n")
    return ("updated", f"{ui.tilde(gitignore)} (+ {path})")
