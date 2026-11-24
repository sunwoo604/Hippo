"""`hippo sync`: fold unmerged patch notes into the feature docs.

Per project, in batches of up to BATCH notes (oldest first):
  1. back up features/ into archive/features-before-sync-<time>/
  2. run the consolidation agent (sandboxed like init; structured data only
     through `hippo feature set` / `hippo decision add|supersede`)
  3. check the result in code: every declared new feature exists with both
     docs, and every Decisions line became exactly one decision
  4. success: move the batch to archive/patches/, record last_consolidated_at
     failure: restore features/ from the backup and keep the notes for next time

Then the git safety net (safety.py) checks commits that reached the default
branch without a note, and moves `last_synced_commit`. The same code runs for `hippo sync` in a repo, `hippo sync --all`, and the
daily job.
"""

import argparse
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from hippo import agent, config, deps, features, paths, prompts, registry, safety, schedule, ui
from hippo.agent import abs_rule
from hippo.errors import HippoError
from hippo.memory import Memory, frontmatter
from hippo.project import Project, current

BATCH = 20
DOC_LIMIT = 20_000  # characters per inlined doc
KEEP_BACKUPS = 5
STALE_STATE_SECONDS = 2 * 24 * 3600
KEEP_LOG_DAYS = 30
TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]
HIPPO_OWNED = safety.HIPPO_OWNED


# --- lock -----------------------------------------------------------------------

class Lock:
    """One sync at a time. A lock left by a process that no longer exists is taken over."""

    def __enter__(self):
        f = paths.lock_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                if _alive(f):
                    raise HippoError("another `hippo sync` is already running.") from None
                f.unlink(missing_ok=True)
        raise HippoError("couldn't take the sync lock.")

    def __exit__(self, *exc):
        paths.lock_file().unlink(missing_ok=True)


def _alive(lock: Path) -> bool:
    try:
        pid = int(lock.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, FileNotFoundError):
        return False
    except PermissionError:
        return True


# --- logging --------------------------------------------------------------------

def log(p_name: str, uuid: str, outcome: str) -> None:
    d = paths.logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    with (d / f"{now:%Y-%m-%d}.log").open("a") as f:
        f.write(f"{now.isoformat(timespec='seconds')} {p_name} ({uuid}) {outcome}\n")


# --- checks ---------------------------------------------------------------------

def decision_lines(note_text: str) -> list[str]:
    """Tagged bullets under '## Decisions' (continuations already folded in)."""
    lines, inside = [], False
    for line in note_text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "decisions"
        elif inside and line.startswith("- ["):
            lines.append(line)
    return lines


def verify(p: Project, batch: list[Path]) -> list[str]:
    problems = []
    found, unreadable = features.load_all(p.dir)
    problems += unreadable
    slugs = {f.slug for f in found}
    for slug in slugs:
        for doc in ("overview.md", "technical.md"):
            if not (p.dir / "features" / slug / doc).exists():
                problems.append(f"features/{slug}/{doc} is missing")
    recorded = {}
    for f in (p.dir / "features").glob("*/decisions.json"):
        for d in Memory(p.dir).decisions(f.parent.name):
            recorded[d.get("source")] = recorded.get(d.get("source"), 0) + 1
    for note in batch:
        text = note.read_text()
        for slug in frontmatter(text).get("new_features", []) or []:
            if slug not in slugs:
                problems.append(f"new feature '{slug}' from {note.name} wasn't created")
        want, got = len(decision_lines(text)), recorded.get(f"patches/{note.name}", 0)
        if want != got:
            problems.append(f"{note.name} has {want} decision(s) but {got} were recorded")
    return problems


# --- one project ------------------------------------------------------------------

def bash_prefixes() -> list[str]:
    h = paths.launcher()
    return [f"{h} feature set", f"{h} decision add", f"{h} decision supersede", f"{h} review add"]


def sandbox(p: Project) -> tuple[list[str], list[str]]:
    allow = ["Read", "Glob", "Grep", abs_rule("Edit", p.dir, "/features/**")]
    allow += [f"Bash({prefix} *)" for prefix in bash_prefixes()]
    deny = agent.secret_denies(p.repo)
    deny += [abs_rule("Edit", p.dir, f"/{f}") for f in HIPPO_OWNED]
    deny.append(abs_rule("Edit", p.repo, "/**"))
    return allow, deny


def touched_slugs(batch: list[Path]) -> list[str]:
    """Features the batch's notes name, in first-seen order."""
    seen = []
    for note in batch:
        for slug in frontmatter(note.read_text()).get("features", []) or []:
            if slug not in seen:
                seen.append(slug)
    return seen


def feature_docs(p: Project, slugs: list[str]) -> str:
    """The current files of the features a batch touches, so the agent needn't read them.

    Features declared new in the batch don't exist yet and are skipped.
    """
    blocks = []
    for slug in slugs:
        d = p.dir / "features" / slug
        if not (d / "feature.json").exists():
            continue
        for name in ("feature.json", "overview.md", "technical.md", "decisions.json"):
            f = d / name
            if f.exists():
                text = f.read_text()
                if len(text) > DOC_LIMIT:
                    text = text[:DOC_LIMIT] + "\n... (truncated; read the file for the rest)"
                blocks.append(f"### features/{slug}/{name}\n\n{text.strip()}")
    return "\n\n".join(blocks) or "None (every feature these notes name is new)."


def build_prompt(p: Project, batch: list[Path]) -> str:
    feats = Memory(p.dir).features()[0]
    lines = [l for l in features.render_index(feats).splitlines() if l.startswith("- ")]
    notes = "\n\n".join(f"### patches/{n.name}\n\n{n.read_text().strip()}" for n in batch)
    return prompts.render("consolidate.md", memory_dir=str(p.dir), repo_path=str(p.repo),
                          hippo=str(paths.launcher()), features="\n".join(lines) or "None yet.",
                          feature_docs=feature_docs(p, touched_slugs(batch)), notes=notes)


def agent_options() -> dict:
    return {"model": config.get("sync_model"), "max_turns": config.get("max_turns")}


def backup(p: Project) -> Path:
    dest = p.dir / "archive" / f"features-before-sync-{datetime.now():%Y-%m-%dT%H%M%S}"
    n = 2
    while dest.exists():
        dest = dest.with_name(dest.name.split("~")[0] + f"~{n}")
        n += 1
    shutil.copytree(p.dir / "features", dest)
    old = sorted((p.dir / "archive").glob("features-before-sync-*"))
    for stale in old[:-KEEP_BACKUPS]:
        shutil.rmtree(stale, ignore_errors=True)
    return dest


def restore(p: Project, saved: Path) -> None:
    shutil.rmtree(p.dir / "features", ignore_errors=True)
    shutil.copytree(saved, p.dir / "features")
    features.write_index(p.dir)


def sync_project(p: Project) -> bool:
    """Returns False if a batch failed (its notes stay for the next run)."""
    name = p.repo.name
    total_notes = total_decisions = 0
    # Snapshot once: notes written while this runs wait for the next sync.
    snapshot = sorted((p.dir / "patches").glob("*.md"))
    for start in range(0, len(snapshot), BATCH):
        batch = snapshot[start:start + BATCH]
        ui.say("sync", f"{name}: folding {len(batch)} patch note(s) into the feature docs")
        saved = backup(p)
        allow, deny = sandbox(p)
        result = agent.run(build_prompt(p, batch), cwd=p.dir, add_dirs=[p.repo], tools=TOOLS,
                           allow=allow, deny=deny, bash_prefixes=bash_prefixes(),
                           on_tool=agent.progress(p.dir, p.repo), **agent_options())
        agent.record("notes", name, p.uuid, result, notes=len(batch))
        problems = [] if result.ok else [f"consolidation agent: {result.error}"]
        if result.ok:
            problems = verify(p, batch)
        if problems:
            restore(p, saved)
            ui.say("failed", f"{name}: {problems[0]}{agent.stats(result)}")
            for line in problems[1:]:
                ui.say("", f"! {line}")
            ui.say("", "feature docs restored; the notes stay and are retried next time")
            log(name, p.uuid, f"failed: {'; '.join(problems)}")
            return False

        dest = p.dir / "archive" / "patches"
        dest.mkdir(parents=True, exist_ok=True)
        decisions = sum(len(decision_lines(n.read_text())) for n in batch)
        for note in batch:
            note.rename(dest / note.name)
        features.write_index(p.dir)
        meta = p.meta()
        meta["last_consolidated_at"] = datetime.now().isoformat(timespec="seconds")
        p.save_meta(meta)
        total_notes += len(batch)
        total_decisions += decisions
        ui.say("updated", f"{name}: {len(batch)} note(s), {decisions} decision(s){agent.stats(result)}")

    if total_notes:
        log(name, p.uuid, f"succeeded: {total_notes} note(s), {total_decisions} decision(s)")
    else:
        ui.say("ok", f"{name}: no new patch notes")
        log(name, p.uuid, "skipped: no new patch notes")
    if not safety.run(p, backup, restore, log, ui.say):
        return False
    meta = p.meta()
    meta["last_synced_at"] = datetime.now().isoformat(timespec="seconds")  # for `hippo status`
    p.save_meta(meta)
    return True


# --- housekeeping -------------------------------------------------------------------

def clean_stale_sessions() -> int:
    """Session state whose SessionEnd never fired (e.g. a closed desktop tab)."""
    removed = 0
    cutoff = time.time() - STALE_STATE_SECONDS
    for f in (paths.root() / "state").glob("*/*.json"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    for d in (paths.root() / "state").glob("*"):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return removed


# --- command ------------------------------------------------------------------------

def targets(all_projects: bool) -> list[Project]:
    if not all_projects:
        return [current(Path.cwd())]
    found = []
    for entry in registry.load():
        repo = Path(entry["repo_path"])
        name = repo.name
        if not repo.is_dir():
            ui.say("skipped", f"{name}: repo not found at {ui.tilde(repo)} (memory kept)")
            log(name, entry["uuid"], f"skipped: repo not found at {repo}")
            continue
        p = Project(entry["uuid"], repo)
        if not p.dir.is_dir():
            ui.say("skipped", f"{name}: memory folder missing ({ui.tilde(p.dir)})")
            log(name, entry["uuid"], "skipped: memory folder missing")
            continue
        found.append(p)
    return found


def prune_logs() -> None:
    cutoff = f"{datetime.now() - timedelta(days=KEEP_LOG_DAYS):%Y-%m-%d}"
    for f in paths.logs_dir().glob("20??-??-??.log"):
        if f.stem < cutoff:
            f.unlink(missing_ok=True)


def sync_all(all_projects: bool) -> list[str]:
    """Names of the projects that failed."""
    with Lock():
        safety.clean_snapshots()
        failed = [p.repo.name for p in targets(all_projects) if not sync_project(p)]
        clean_stale_sessions()
        prune_logs()
    return failed


def scheduled() -> int:
    """The daily job (launchd): nobody is watching, so never prompt, and notify on failure."""
    now = datetime.now()
    at = config.get("sync_at")
    if not schedule.due(now, at):
        print(f"{now:%Y-%m-%d %H:%M} skipped: already ran since {schedule.last_slot(now, at):%H:%M}")
        return 0
    schedule.mark_ran(now)
    schedule.trim_launchd_log()
    print(f"--- {now:%Y-%m-%d %H:%M} daily sync", flush=True)
    if not deps.claude_logged_in():
        log("hippo", "-", "daily sync skipped: Claude Code is logged out")
        schedule.save_outcome(False, "skipped: Claude Code is logged out")
        schedule.notify("Daily sync skipped: Claude Code is logged out. Run `claude auth login`.")
        return 1
    try:
        failed = sync_all(True)
    except HippoError as e:  # e.g. a manual sync holds the lock
        log("hippo", "-", f"daily sync skipped: {e}")
        schedule.save_outcome(True, f"skipped: {e}")
        return 0
    except Exception as e:
        schedule.save_outcome(False, f"crashed: {e}")
        schedule.notify(f"Daily sync crashed: {e}. See ~/agent-memory/logs/launchd.log.")
        raise
    if failed:
        names = ", ".join(failed)
        schedule.save_outcome(False, f"failed for {names}")
        schedule.notify(f"Daily sync failed for {names}. Run `hippo sync` in "
                        f"{'that repo' if len(failed) == 1 else 'each'} to see why.")
        return 1
    schedule.save_outcome(True, "ok")
    return 0


def run(args: argparse.Namespace) -> int:
    if os.environ.get("HIPPO_AGENT"):
        raise HippoError("`hippo sync` can't be run by Hippo's own agents.")
    if getattr(args, "scheduled", False):
        return scheduled()
    return 1 if sync_all(args.all) else 0
