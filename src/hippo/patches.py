"""Patch notes: a session's record of what changed, why, and what the user decided.

Session agents never write patch files by hand. They pipe the note's body into
`hippo patch new`, which checks it against the project's features and git,
fills in the frontmatter, and picks a collision-free filename.

  ---
  author: Sunwoo Kim
  created_at: 2026-10-17T14:30:12
  commits: [a1b2c3d]
  features: [billing, reports]
  new_features: [reports]
  ---
  ## Changed
  - [reports] New CSV export of monthly usage per account.
  - [billing] Exposes per-account usage totals for the report.
  ## Why
  ...
  ## Decisions
  ...
  ## Follow-ups
  ...
  ## New features
  - [reports] Monthly usage reports that admins can download as CSV.
"""

import argparse
import difflib
import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from hippo import features, git, ui
from hippo.errors import HippoError
from hippo.fsutil import atomic_write_text
from hippo.memory import Memory
from hippo.project import Project, resolve

SECTIONS = ("Changed", "Why", "Decisions", "Follow-ups")
MAY_BE_NONE = ("Decisions", "Follow-ups")
TAGGED = re.compile(r"^- \[(?P<slug>[^\]]+)\]\s+(?P<text>\S.*)$")
SIMILAR = 0.8  # difflib ratio above which a new slug looks like an existing one


# --- parsing the body -------------------------------------------------------

def parse_body(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """`## Section` -> list of entries (a bullet plus its indented continuation lines).

    Returns (sections, errors). A section whose only content is "None" is [].
    """
    sections: dict[str, list[str]] = {}
    errors, current = [], None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            name = _canonical(line[3:].strip())
            if name is None:
                errors.append(f"unknown section '{line}' (use only: "
                              + ", ".join(f"## {s}" for s in SECTIONS) + ")")
            elif name in sections:
                errors.append(f"section '## {name}' appears twice")
            current = name
            if name is not None:
                sections.setdefault(name, [])
        elif not line.strip() or current is None:
            if line.strip() and current is None:
                errors.append(f"text before the first section: '{line.strip()[:40]}'")
        elif line.strip().lower() in ("none", "none.", "- none"):
            continue
        elif line.startswith("- "):
            sections[current].append(line)
        elif raw.startswith((" ", "\t")) and sections[current]:
            sections[current][-1] += " " + line.strip()
        else:
            errors.append(f"under '## {current}', start each item with '- [feature] ': "
                          f"'{line.strip()[:40]}'")
    for name in SECTIONS:
        if name not in sections:
            errors.append(f"missing section '## {name}'"
                          + (" (write None if there is nothing)" if name in MAY_BE_NONE else ""))
    return sections, errors


def _canonical(heading: str) -> str | None:
    for name in SECTIONS:
        if heading.lower().replace(" ", "-") == name.lower():
            return name
    return None


# --- validation -------------------------------------------------------------

def known_slugs(mem: Memory) -> tuple[set[str], set[str]]:
    """(existing features, new features declared by unmerged patches)."""
    existing = {f.slug for f in mem.features()[0]}
    pending = {s for p in mem.patches() for s in p.meta.get("new_features", []) or []}
    return existing, pending - existing


def validate(feature_args: list[str], new_args: list[tuple[str, str]], body: str,
             mem: Memory) -> tuple[dict[str, list[str]], list[str]]:
    errors = []
    existing, pending = known_slugs(mem)
    known = existing | pending

    for slug in feature_args:
        if slug not in known:
            close = difflib.get_close_matches(slug, known, n=1)
            hint = f"did you mean '{close[0]}'? " if close else ""
            errors.append(f"--feature '{slug}' doesn't exist; {hint}"
                          f"if it's a brand-new feature, use --new-feature {slug} \"<description>\"")

    for slug, desc in new_args:
        if not features.SLUG.match(slug):
            errors.append(f"--new-feature '{slug}' must be lowercase kebab-case, e.g. 'usage-reports'")
        elif slug in existing:
            errors.append(f"--new-feature '{slug}' already exists; use --feature {slug}")
        elif slug in pending:
            errors.append(f"--new-feature '{slug}' was already declared by another unmerged "
                          f"patch; use --feature {slug}")
        else:
            close = [k for k in known if difflib.SequenceMatcher(None, slug, k).ratio() >= SIMILAR]
            if close:
                errors.append(f"--new-feature '{slug}' looks like existing feature '{close[0]}'; "
                              f"if it really is separate, choose a clearer name")
        if not desc.strip() or "\n" in desc:
            errors.append(f"--new-feature '{slug}' needs a one-line description")

    named = feature_args + [s for s, _ in new_args]
    if not named:
        errors.append("name at least one --feature or --new-feature")
    dupes = {s for s in named if named.count(s) > 1}
    if dupes:
        errors.append(f"feature named more than once: {', '.join(sorted(dupes))}")

    sections, body_errors = parse_body(body)
    errors += body_errors
    if not sections.get("Why"):
        errors.append("'## Why' can't be empty: say why the change was made")

    changed_tags = set()
    for name, entries in sections.items():
        for entry in entries:
            m = TAGGED.match(entry)
            if not m:
                errors.append(f"under '## {name}', tag the item with its feature, "
                              f"like '- [{named[0] if named else 'auth'}] ...': '{entry[:50]}'")
                continue
            if m["slug"] not in named:
                errors.append(f"'[{m['slug']}]' under '## {name}' isn't one of the "
                              f"--feature/--new-feature values")
            if name == "Changed":
                changed_tags.add(m["slug"])
            if name == "Decisions" and "why:" not in m["text"].lower():
                errors.append(f"decision for [{m['slug']}] needs a 'Why: ...' "
                              f"(only record choices the user explicitly agreed to)")
    for slug in named:
        if slug not in changed_tags and slug not in dupes:
            errors.append(f"feature '{slug}' has no '- [{slug}] ...' line under '## Changed'")
    return sections, errors


# --- git ----------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str | None:
    r = subprocess.run(["git", *args], cwd=repo, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def resolve_commits(repo: Path, values: list[str]) -> tuple[list[str], list[str]]:
    hashes, errors = [], []
    for v in values:
        for h in filter(None, (x.strip() for x in v.split(","))):
            short = _git(repo, "rev-parse", "--short=7", "--verify", "--quiet", f"{h}^{{commit}}")
            if short is None:
                errors.append(f"--commit '{h}' isn't a commit in this repo")
            elif short not in hashes:
                hashes.append(short)
    return hashes, errors


def _author(repo: Path) -> str:
    """Git's own author name (honours GIT_AUTHOR_NAME as well as user.name)."""
    ident = _git(repo, "var", "GIT_AUTHOR_IDENT") or ""
    return ident.split(" <")[0].strip() or "unknown"


def changed_files(repo: Path, commits: list[str]) -> set[str]:
    """Files touched by the given commits plus anything uncommitted."""
    files = set()
    for c in commits:
        out = _git(repo, "show", "--name-only", "--format=", c)
        files |= set(filter(None, (out or "").splitlines()))
    # Plain name lists, not `status --porcelain`: its leading status columns are
    # fragile to parse (stripping the output shifts the first line's path).
    tracked = _git(repo, "diff", "--name-only", "HEAD")
    if tracked is None:  # no commits yet
        tracked = "\n".join(filter(None, (_git(repo, "diff", "--name-only"),
                                          _git(repo, "diff", "--name-only", "--cached"))))
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard") or ""
    files |= set(filter(None, tracked.splitlines() + untracked.splitlines()))
    return files


NOTED_FILES = "noted-files.json"


def noted_content(memory_dir: Path) -> dict[str, dict[str, str]]:
    """path -> {blob id the file had when a note was written: note file name}."""
    f = memory_dir / NOTED_FILES
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except json.JSONDecodeError:
        return {}


def record_content(memory_dir: Path, blobs: dict[str, str], note_name: str) -> None:
    """Remember what each changed file looked like when this note was written, so a
    commit made later with exactly that content counts as explained by the note
    (committed after the session, rebased, cherry-picked or squash-merged)."""
    noted = noted_content(memory_dir)
    for path, blob in blobs.items():
        noted.setdefault(path, {})[blob] = note_name
    atomic_write_text(memory_dir / NOTED_FILES, json.dumps(noted, indent=1, sort_keys=True) + "\n")


def owners(path: str, feats: list[features.Feature]) -> set[str]:
    return {f.slug for f in feats for k in f.key_files
            if k.path == path or fnmatch.fnmatch(path, k.path)}


# --- writing ----------------------------------------------------------------

def frontmatter(meta: dict) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: [{', '.join(value)}]" if isinstance(value, list) else f"{key}: {value}")
    return "\n".join(lines + ["---"])


def render(meta: dict, sections: dict[str, list[str]], new_args: list[tuple[str, str]]) -> str:
    parts = [frontmatter(meta)]
    for name in SECTIONS:
        entries = sections.get(name) or ["None"]
        parts.append(f"## {name}\n" + "\n".join(entries))
    if new_args:
        parts.append("## New features\n" + "\n".join(f"- [{s}] {d.strip()}" for s, d in new_args))
    return "\n\n".join(parts) + "\n"


def target_file(patches_dir: Path, now: datetime) -> Path:
    stem = now.strftime("%Y-%m-%dT%H%M%S")
    f, n = patches_dir / f"{stem}.md", 2
    while f.exists():
        f, n = patches_dir / f"{stem}-{n}.md", n + 1
    return f


def run_new(args: argparse.Namespace) -> int:
    if os.environ.get("HIPPO_AGENT"):
        raise HippoError("patch notes come from coding sessions, not Hippo's own agents.")
    if sys.stdin.isatty():
        raise HippoError("pipe the note's body on stdin, e.g. hippo patch new --feature auth <<'EOF' ... EOF")
    p: Project = resolve(Path.cwd())
    mem = Memory(p.dir)
    body = sys.stdin.read()
    feature_args = args.feature or []
    new_args = [tuple(x) for x in args.new_feature or []]

    sections, errors = validate(feature_args, new_args, body, mem)
    commits, commit_errors = resolve_commits(p.repo, args.commit or [])
    errors += commit_errors
    if errors:
        raise HippoError(
            "patch note was not saved:\n" + "\n".join(f"  - {e}" for e in errors),
            next_steps=["Fix the problems above and run the same `hippo patch new` again."],
        )

    named = feature_args + [s for s, _ in new_args]
    now = datetime.now()
    meta = {
        "author": _author(p.repo),
        "created_at": now.isoformat(timespec="seconds"),
        "commits": commits,
        "features": named,
    }
    if new_args:
        meta["new_features"] = [s for s, _ in new_args]
    target = target_file(p.dir / "patches", now)
    atomic_write_text(target, render(meta, sections, new_args))
    touched = changed_files(p.repo, commits)
    record_content(p.dir, git.blob_hashes(p.repo, touched), target.name)
    ui.say("created", f"patches/{target.name} (features: {', '.join(named)})")

    feats = mem.features()[0]
    missed = {}
    for path in sorted(touched):
        for slug in owners(path, feats) - set(named):
            missed.setdefault(slug, []).append(path)
    for slug, paths in missed.items():
        shown = ", ".join(paths[:3]) + (f" and {len(paths) - 3} more" if len(paths) > 3 else "")
        ui.say("warning", f"{shown} changed and belong to '{slug}', which this note doesn't name. "
                          f"If '{slug}' really changed, write another note for it.")
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("patch", help="write session patch notes (used by agents)")
    psub = p.add_subparsers(dest="patch_command", required=True, metavar="<action>")
    n = psub.add_parser("new", help="save a patch note; the body comes on stdin")
    n.add_argument("--feature", action="append", metavar="SLUG",
                   help="an existing feature this change touched (repeatable)")
    n.add_argument("--new-feature", action="append", nargs=2, metavar=("SLUG", "DESCRIPTION"),
                   help="a brand-new feature created in this session (repeatable)")
    n.add_argument("--commit", "--commits", action="append", metavar="HASH",
                   help="commit you made (repeatable or comma-separated)")
    n.set_defaults(func=run_new)
