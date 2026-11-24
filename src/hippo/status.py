"""`hippo status`: every linked repo at a glance, plus the daily job. Read-only."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from hippo import config, deps, git, paths, project, registry, review, safety, schedule, ui
from hippo.errors import HippoError
from hippo.project import Project

HEADERS = ("repo", "last sync", "unsynced notes", "needs review", "new commits")


def run(args: argparse.Namespace) -> int:
    if not paths.config_file().exists():
        raise HippoError("Hippo isn't set up on this machine yet.",
                         next_steps=["Run `hippo install`."])
    entries = registry.load()
    if not entries:
        print("No repos linked yet.")
    else:
        rows, problems = [], []
        for e in entries:
            row, problem = _row(e)
            rows.append(row)
            if problem:
                problems.append(problem)
        _table(rows)
        for line in problems:
            print(f"! {line}")
    print()
    for label, value in _machine():
        print(f"{label:<12}{value}")
    if not entries:
        ui.next_steps(["Run `hippo link` inside a repo to link it."])
    return 0


def _row(entry: dict) -> tuple[list[str], str | None]:
    """Table cells for one linked repo, and a line saying how to fix it if it's broken."""
    repo, uuid = Path(entry["repo_path"]), entry["uuid"]
    name, p = repo.name, Project(entry["uuid"], repo)
    blank = ["-"] * 4
    if not p.dir.is_dir():
        return [name, "memory missing", *blank[1:]], (
            f"{name}: memory folder missing ({ui.tilde(p.dir)}). Run `hippo unlink {uuid}`.")
    try:
        meta = p.meta()
    except (OSError, json.JSONDecodeError):
        return [name, "meta.json unreadable", *blank[1:]], f"{name}: {ui.tilde(p.meta_file)} unreadable."
    last = meta.get("last_synced_at") or meta.get("last_consolidated_at")
    cells = [name, _when(last) if last else "never",
             str(len(list((p.dir / "patches").glob("*.md")))),
             str(len(review.open_items(p.dir)))]
    if not repo.is_dir():
        return cells + ["-"], (f"{name}: repo not found at {ui.tilde(repo)} (moved or deleted?). "
                               f"Run `hippo unlink {uuid[:8]}` to archive its memory.")
    if project.dot_uuid(repo) != uuid:
        return cells + ["-"], (f"{name}: {ui.tilde(repo)} is no longer linked to this memory. "
                               f"Run `hippo unlink {uuid[:8]}` to archive it.")
    return cells + [_new_commits(p, meta)], None


def _new_commits(p: Project, meta: dict) -> str:
    """Commits on the default branch since the last sync (local refs only; never fetches)."""
    try:
        tip = git.branch_tip(p.repo, meta.get("default_branch") or git.default_branch(p.repo))
        if tip is None:
            return "0"
        commits, warning = safety.commit_range(p, tip)
    except (HippoError, OSError, KeyError):
        return "?"
    return f"{len(commits)}{'+' if warning else ''}"


def _when(iso: str) -> str:
    try:
        return f"{datetime.fromisoformat(iso):%Y-%m-%d %H:%M}"
    except ValueError:
        return iso


def _table(rows: list[list[str]]) -> None:
    widths = [max(len(r[i]) for r in [list(HEADERS), *rows]) for i in range(len(HEADERS))]
    for r in [list(HEADERS), *rows]:
        print("  ".join(c.ljust(w) if i == 0 or i == 1 else c.rjust(w)
                        for i, (c, w) in enumerate(zip(r, widths))).rstrip())


def _machine() -> list[tuple[str, str]]:
    return [("daily sync", _schedule()), ("claude", _login())]


def _schedule() -> str:
    if not schedule.supported():
        return "macOS only for now; run `hippo sync --all` yourself"
    if not config.get("schedule"):
        return "off (`hippo install --schedule` turns it on)"
    at = config.get("sync_at")
    if not schedule.loaded():
        return f"{at}, NOT loaded: run `hippo install`"
    parts = [f"{at}, loaded"]
    outcome = schedule.last_outcome()
    if outcome:
        parts.append(f"last run {_when(outcome['at'])}: {outcome['detail']}")
    else:  # runs from before outcomes were recorded: launchd.log has their headers
        log = schedule.launchd_log()
        runs = [l for l in log.read_text().splitlines() if l.startswith("--- ")] if log.exists() else []
        parts.append(f"last run {runs[-1][4:20]}" if runs else "hasn't run yet")
    return " · ".join(parts)


def _login() -> str:
    try:
        ok = deps.claude_logged_in()
    except HippoError:
        return "`claude auth status` didn't respond"
    except OSError:
        return "not found on PATH"
    return "logged in" if ok else "logged out: run `claude auth login` (sync can't run)"
