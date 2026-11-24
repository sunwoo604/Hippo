"""`hippo install`: one-time, idempotent machine setup."""

import argparse
import os
from pathlib import Path

from hippo import __version__, claude_settings, config, deps, launcher, paths, project, registry, schedule, ui
from hippo.errors import HippoError
from hippo.fsutil import atomic_write_text


def run(args: argparse.Namespace) -> int:
    settings = _schedule_settings(args)  # check flags before changing anything
    deps.require_tools("git", "claude")
    deps.require_claude_login()
    ui.say("ok", "git and claude found; Claude Code is logged in")

    root = paths.root()
    existed = root.exists()
    for d in (paths.bin_dir(), paths.logs_dir(), paths.projects_dir()):
        d.mkdir(parents=True, exist_ok=True)
    if not paths.registry_file().exists():
        atomic_write_text(paths.registry_file(), "[]\n")
    ui.say("ok" if existed else "created", f"{ui.tilde(root)}/")

    route = _record_route()

    if paths.legacy_prompts_dir().exists():
        ui.say("note", f"{ui.tilde(paths.legacy_prompts_dir())}/ is no longer used "
                       "(prompts ship with Hippo); you can delete it")

    for status, what in launcher.install(route):
        ui.say(status, ui.tilde(what) if isinstance(what, Path) else what)

    _refresh_linked_repos()

    _setup_schedule(settings)

    hippo = "hippo" if launcher.on_path() else ui.tilde(paths.launcher())
    ui.next_steps([f"In a terminal, go to a project folder and run:  {hippo} link"])
    return 0


def _refresh_linked_repos() -> None:
    """Keep each linked repo's Hippo hooks pointing at the current launcher."""
    for entry in registry.load():
        repo = Path(entry["repo_path"])
        if not repo.is_dir():
            continue  # repo moved or deleted: nothing to refresh
        try:
            if project.current(repo).uuid != entry["uuid"]:
                continue
        except HippoError:
            continue  # no longer linked to this memory
        status, detail = claude_settings.ensure(repo)
        ui.say(status, f"{ui.tilde(claude_settings.settings_file(repo))}: {detail}")


def _record_route() -> str:
    """Remember how Hippo was installed, so later runs don't double-register anything."""
    route = "plugin" if os.environ.get("HIPPO_ROUTE") == "plugin" else "cli"
    cfg = config.load()
    previous = cfg.get("route")
    cfg.update(route=route, version=__version__)
    config.save(cfg)
    if previous and previous != route:
        ui.say("updated", f"install route: {previous} -> {route}")
    else:
        ui.say("ok", f"install route: {route}")
    return route


def _schedule_settings(args: argparse.Namespace) -> dict:
    changes = {}
    model = getattr(args, "sync_model", None)
    if model is not None:
        model = model.strip()
        if not model or any(c.isspace() for c in model):
            raise HippoError("--sync-model needs a model name, e.g. sonnet or haiku.")
        changes["sync_model"] = model
    at = getattr(args, "sync_at", None)
    if at is not None:
        hour, minute = schedule.parse_time(at)
        changes["sync_at"] = f"{hour:02d}:{minute:02d}"
    if getattr(args, "no_schedule", False):
        changes["schedule"] = False
    elif getattr(args, "schedule", False) or at is not None:
        changes["schedule"] = True
    return changes


def _setup_schedule(changes: dict) -> None:
    cfg = config.load()
    cfg.update(changes)
    config.save(cfg)
    if not schedule.supported():
        ui.say("note", "daily sync: macOS only for now; run `hippo sync --all` yourself (e.g. from cron)")
        return
    if not config.get("schedule"):
        removed = schedule.remove()
        ui.say("removed" if removed else "ok",
               "daily sync is off (`hippo install --schedule` turns it back on)")
        return
    status, detail = schedule.install(config.get("sync_at"))
    ui.say(status, f"{detail}: model {config.get('sync_model')}, "
                   f"up to {config.get('max_turns')} turns per agent run")
