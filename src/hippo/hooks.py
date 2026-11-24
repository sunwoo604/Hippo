"""Claude Code hook handlers: `hippo _hook session-start | stop | session-end`.

Claude Code runs these automatically (never the model, never the user). Each
reads the event JSON on stdin and, in repos without `.hippo` or inside Hippo's
own agents, exits silently.

  session-start  adds the project's memory to the session; records where the
                 session started (HEAD + a fingerprint of the files changed since)
  stop           after each reply: if code changed since the last patch note
                 and nothing has been recorded, block once and ask for a note
  session-end    if the session leaves changes no patch note covers, write an
                 "unnoted session" marker for the nightly job

A hook must never break the user's session: any internal error is logged to
~/agent-memory/logs/hooks.log and the hook exits 0 with no output.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from hippo import features, git, paths, prompts
from hippo.errors import HippoError
from hippo.fsutil import atomic_write_text
from hippo.memory import Memory
from hippo.patches import changed_files, known_slugs, owners
from hippo.project import Project, current

CONTEXT_LIMIT = 9500  # Claude Code caps hook context at 10,000 characters
EVENTS = ("session-start", "stop", "session-end")


# --- shared helpers -----------------------------------------------------------

def _git(repo: Path, *args: str) -> str | None:
    r = subprocess.run(["git", *args], cwd=repo, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def head(repo: Path) -> str | None:
    return _git(repo, "rev-parse", "--verify", "--quiet", "HEAD")


def fingerprint(repo: Path, base: str | None) -> str:
    """What the work since `base` (the session's starting HEAD) looks like now.

    Built from file contents, not commits: committing work that already has a
    note leaves it unchanged, so the session isn't asked for a second note.
    """
    if base:
        tracked = _git(repo, "diff", "--name-only", base) or ""
    else:  # no commits when the session started
        tracked = _git(repo, "ls-files") or ""
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard") or ""
    files = filter(None, tracked.splitlines() + untracked.splitlines())
    h = hashlib.sha256()
    for path, blob in sorted(git.blob_hashes(repo, files).items()):
        h.update(f"{path}\0{blob}\n".encode())
    return h.hexdigest()


def state_file(p: Project, session_id: str) -> Path:
    return paths.root() / "state" / p.uuid / f"{session_id}.json"


def load_state(p: Project, session_id: str) -> dict | None:
    f = state_file(p, session_id)
    return json.loads(f.read_text()) if f.exists() else None


def save_state(p: Project, session_id: str, state: dict) -> None:
    atomic_write_text(state_file(p, session_id), json.dumps(state, indent=2) + "\n")


def new_state(p: Project) -> dict:
    start = head(p.repo)
    fp = fingerprint(p.repo, start)
    return {"started_at": datetime.now().isoformat(timespec="seconds"),
            "start_head": start, "noted_fp": fp, "seen_fp": fp, "mark": time.time()}


def notes_since(p: Project, mark: float) -> list[Path]:
    return [f for f in (p.dir / "patches").glob("*.md") if f.stat().st_mtime >= mark]


def unnoted_commits(p: Project, state: dict) -> list[str]:
    """Commits made since the session started that no unmerged patch lists."""
    rng = f"{state['start_head']}..HEAD" if state.get("start_head") else "HEAD"
    hashes = (_git(p.repo, "rev-list", "--abbrev-commit", "--abbrev=7", rng) or "").split()
    noted = {c for patch in Memory(p.dir).patches() for c in patch.meta.get("commits", []) or []}
    return [h for h in reversed(hashes) if not any(h.startswith(n) or n.startswith(h) for n in noted)]


# --- session-start ------------------------------------------------------------

def session_start(event: dict, p: Project) -> dict | None:
    sid = event.get("session_id", "unknown")
    # A resumed or compacted session keeps its original starting point.
    if event.get("source") in ("startup", "clear", None) or load_state(p, sid) is None:
        save_state(p, sid, new_state(p))
    return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                   "additionalContext": context(p)}}


def context(p: Project) -> str:
    mem = Memory(p.dir)
    feats, _ = mem.features()
    pending = sorted(known_slugs(mem)[1])
    lines = [line for line in features.render_index(feats).splitlines() if line.startswith("- ")]
    lines += [f"- {slug}: new, declared in an unmerged patch note" for slug in pending]
    feature_text = "\n".join(lines) or "None yet."

    notes = mem.patches()
    note_lines = []
    for n in reversed(notes):  # newest first
        changed = next((l for l in n.path.read_text().splitlines() if l.startswith("- [")), "")
        note_lines.append(f"- {n.path.name} ({', '.join(n.features)}): {changed[2:][:120]}")

    def render(shown: list[str]) -> str:
        hidden = len(note_lines) - len(shown)
        body = "\n".join(shown) or "None."
        if hidden:
            body += f"\n- ...and {hidden} older notes in patches/"
        return prompts.render("session-context.md", memory_dir=str(p.dir),
                              features=feature_text, patches=body, hippo=str(paths.launcher()))

    shown = note_lines
    text = render(shown)
    while len(text) > CONTEXT_LIMIT and shown:
        shown = shown[:-1]
        text = render(shown)
    return text[:CONTEXT_LIMIT]


# --- stop -----------------------------------------------------------------------

def stop(event: dict, p: Project) -> dict | None:
    sid = event.get("session_id", "unknown")
    state = load_state(p, sid)
    if state is None:  # hooks were registered mid-session: start watching from now
        save_state(p, sid, new_state(p))
        return None

    fp = fingerprint(p.repo, state.get("start_head"))
    if notes_since(p, state["mark"]):
        state.update(noted_fp=fp, seen_fp=fp, mark=time.time())
        save_state(p, sid, state)
        return None
    if fp == state["seen_fp"]:
        return None
    if event.get("stop_hook_active"):
        # We already asked once and Claude stopped anyway ("nothing meaningful").
        # Don't ask again until something else changes.
        state.update(seen_fp=fp, mark=time.time())
        save_state(p, sid, state)
        return None
    return {"decision": "block", "reason": note_request(p, state)}


def note_request(p: Project, state: dict) -> str:
    feats = Memory(p.dir).features()[0]
    likely = sorted({o for f in changed_files(p.repo, []) | _files_of(p, state)
                     for o in owners(f, feats)})
    commits = unnoted_commits(p, state)
    flags = " ".join(f"--feature {s}" for s in likely) or "--feature <slug>"
    flags += "".join(f" --commit {c}" for c in commits)
    known = ", ".join(f.slug for f in feats) or "none yet"
    return f"""Hippo: this task changed code and has no patch note yet. If the task is done and the change is meaningful, record it now with one Bash call:

{paths.launcher()} patch new {flags} <<'EOF'
## Changed
- [<slug>] what changed
## Why
- [<slug>] why it changed (usually what the user asked for)
## Decisions
None
## Follow-ups
None
EOF

- Tag every line with its feature. Known features: {known}.{' Likely from the files you changed: ' + ', '.join(likely) + '.' if likely else ''}
- A brand-new feature: use --new-feature <slug> "<one-line description>" instead of --feature.
- Under Decisions, list only choices the user explicitly agreed to in this session, as: - [<slug>] the choice. Why: ... Rejected: ...
- Write the note only once the task is done: the user confirmed the result, or the change is clearly complete. If the work isn't confirmed yet (for example you are debugging, a fix hasn't been tested, the user is likely to try it and come back, or you are waiting for their answer), don't write a note: reply only "Still mid-task; I'll write the note once it's done." The same applies if nothing meaningful changed. You won't be asked again for these changes, and you'll be asked again once more code changes."""


def _files_of(p: Project, state: dict) -> set[str]:
    rng = f"{state['start_head']}..HEAD" if state.get("start_head") else "HEAD"
    out = _git(p.repo, "diff", "--name-only", rng) if state.get("start_head") else \
        _git(p.repo, "ls-files")
    return set(filter(None, (out or "").splitlines()))


# --- session-end ----------------------------------------------------------------

def session_end(event: dict, p: Project) -> dict | None:
    sid = event.get("session_id", "unknown")
    state = load_state(p, sid)
    if state is None:
        return None
    fp = fingerprint(p.repo, state.get("start_head"))
    if notes_since(p, state["mark"]):
        state["noted_fp"] = fp
    if fp != state["noted_fp"]:
        marker = {
            "session_id": sid,
            "started_at": state["started_at"],
            "ended_at": datetime.now().isoformat(timespec="seconds"),
            "end_reason": event.get("reason"),
            "transcript_path": event.get("transcript_path"),
            "start_head": state.get("start_head"),
            "end_head": head(p.repo),
            "unnoted_commits": unnoted_commits(p, state),
            "changed_files": sorted(changed_files(p.repo, []) | _files_of(p, state)),
        }
        atomic_write_text(p.dir / "unnoted" / f"{sid}.json", json.dumps(marker, indent=2) + "\n")
    state_file(p, sid).unlink(missing_ok=True)
    return None


# --- entry point ----------------------------------------------------------------

HANDLERS = {"session-start": session_start, "stop": stop, "session-end": session_end}


def main(event_name: str) -> int:
    try:
        if os.environ.get("HIPPO_AGENT"):
            return 0
        event = json.loads(sys.stdin.read() or "{}")
        try:
            p = current(Path(event.get("cwd") or os.getcwd()))
        except HippoError:
            return 0  # not a linked repo (or memory missing): stay out of the way
        out = HANDLERS[event_name](event, p)
        if out:
            print(json.dumps(out))
    except Exception:
        _log_failure(event_name)
    return 0


def _log_failure(event_name: str) -> None:
    try:
        log = paths.logs_dir() / "hooks.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {event_name}\n"
                    f"{traceback.format_exc()}\n")
    except Exception:
        pass
