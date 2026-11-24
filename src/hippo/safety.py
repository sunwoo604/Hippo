"""The git safety net: the second pass of `hippo sync`, after the patch notes.

Finds commits that reached the default branch since `last_synced_commit` with no
session note explaining them (work done outside Claude, or a skipped note), and
has an agent bring the feature docs up to date from their diffs.

In code, not by the model:
  - the range: last_synced_commit .. tip of the default branch (local or
    origin's copy, whichever is ahead; never fetches)
  - which commits are explained: listed in a note (unmerged or archived), or
    every file they change has exactly the content some note recorded
  - which features a commit touches (their key files), and review items for
    files no feature claims and key files missing from the branch
  - diffs without secret files, lockfiles or build output, within size caps
  - a read-only snapshot of the branch (git archive) instead of the working
    copy, so the agent sees what is on main and nothing uncommitted
  - the check afterwards: no feature created or removed, no decision changed
The agent only rewrites prose (and may fix an existing feature's key files).
`last_synced_commit` moves to the tip only when the whole pass succeeds.
"""

import fnmatch
import shutil
import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath

from hippo import agent, features, git, paths, prompts, review
from hippo.agent import SECRET_DIRS, SECRET_PATTERNS, abs_rule
from hippo.memory import Memory, frontmatter
from hippo.patches import noted_content, owners
from hippo.project import Project

BATCH = 30
MAX_BATCHES = 3
UNREACHABLE_LIMIT = 50
DIFF_LINES = 300          # per commit
BATCH_DIFF_CHARS = 40_000  # after this, later commits in the batch show only their file list
TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]
HIPPO_OWNED = ("index.md", "meta.json", "needs-review.json", "noted-files.json",
               "features/*/feature.json", "features/*/decisions.json",
               "patches/**", "archive/**", "unnoted/**")

# Never documented and never shown to the agent: noise, not features.
NOISE_PATTERNS = (
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "*.lock",
    "go.sum", "*.min.js", "*.min.css", "*.map", "*.pyc", ".DS_Store",
)
NOISE_DIRS = ("node_modules", "dist", "build", "__pycache__", ".venv", "venv")


# --- which files count ------------------------------------------------------------

def _matches(path: str, patterns, dirs) -> bool:
    parts = PurePosixPath(path).parts
    return any(fnmatch.fnmatch(parts[-1], p) for p in patterns) or any(d in parts[:-1] for d in dirs)


def is_secret(path: str) -> bool:
    return _matches(path, SECRET_PATTERNS, SECRET_DIRS)


def is_noise(path: str) -> bool:
    return is_secret(path) or _matches(path, NOISE_PATTERNS, NOISE_DIRS)


def excludes() -> list[str]:
    """Pathspecs that keep secret and noise files out of every diff Hippo shows."""
    specs = [f":(exclude,glob)**/{p}" for p in SECRET_PATTERNS + NOISE_PATTERNS]
    specs += [f":(exclude,glob)**/{d}/**" for d in SECRET_DIRS + NOISE_DIRS]
    return specs


# --- the range of commits -------------------------------------------------------------

def commit_range(p: Project, tip: str) -> tuple[list[str], str | None]:
    """Commits (oldest first, no merges) since last_synced_commit, plus a warning if any."""
    meta = p.meta()
    start = meta.get("last_synced_commit")
    base = ["rev-list", "--reverse", "--no-merges"]
    if start and git.is_ancestor(p.repo, start, tip):
        return git.lines(p.repo, *base, f"{start}..{tip}"), None
    if not start:  # no commits when the repo was linked
        return git.lines(p.repo, *base, f"--since={meta['created_at']}", tip), None
    since = meta.get("last_consolidated_at") or meta["created_at"]
    commits = git.lines(p.repo, *base, f"--max-count={UNREACHABLE_LIMIT}", f"--since={since}", tip)
    return commits, (f"last synced commit {start[:7]} is no longer on the branch (rebased or "
                     f"force-pushed?); checked up to {UNREACHABLE_LIMIT} commits since {since[:10]}")


def noted_commits(memory_dir: Path) -> set[str]:
    found = set()
    for folder in (memory_dir / "patches", memory_dir / "archive" / "patches"):
        for note in folder.glob("*.md"):
            found |= set(frontmatter(note.read_text()).get("commits", []) or [])
    return found


def unexplained(p: Project, commits: list[str]) -> list[tuple[str, list[str]]]:
    """(commit, its meaningful files) for each commit no note explains."""
    listed = noted_commits(p.dir)
    content = noted_content(p.dir)
    out = []
    for c in commits:
        if any(c.startswith(h) for h in listed):
            continue
        blobs = {f: b for f, b in git.commit_blobs(p.repo, c).items() if not is_noise(f)}
        if not blobs:
            continue  # only lockfiles, build output, secrets...
        if all(b in content.get(f, {}) for f, b in blobs.items()):
            continue
        out.append((c, sorted(blobs)))
    return out


# --- review items found in code ---------------------------------------------------------

def _folder(path: str) -> str:
    parts = PurePosixPath(path).parts
    return "/".join(parts[:2]) + "/" if len(parts) > 2 else (parts[0] + "/" if len(parts) == 2 else "./")


def findings(p: Project, tip: str, branch: str, todo: list[tuple[str, list[str]]],
             feats: list[features.Feature]) -> list[dict]:
    on_branch = set(git.tree_files(p.repo, tip))
    found = []
    # Files no feature claims, one item per folder. Earlier items are re-checked too.
    groups: dict[str, dict] = {}
    for c, files in todo:
        for f in files:
            if f in on_branch and not owners(f, feats):
                g = groups.setdefault(_folder(f), {"files": set(), "commits": set()})
                g["files"].add(f)
                g["commits"].add(c[:7])
    for item in review.open_items(p.dir):
        if item.get("kind") == "unclaimed" and item["path"] not in groups:
            still = [f for f in item.get("files", []) if f in on_branch and not owners(f, feats)]
            if still:
                groups[item["path"]] = {"files": set(still), "commits": set(item.get("commits", []))}
    for folder, g in sorted(groups.items()):
        n = len(g["files"])
        found.append({"kind": "unclaimed", "path": folder, "files": sorted(g["files"]),
                      "commits": sorted(g["commits"]),
                      "detail": f"{n} file{'s' if n > 1 else ''} in {folder} changed without a "
                                f"session note and no feature lists {'them' if n > 1 else 'it'}"})
    # Key files that aren't on the branch.
    for feat in feats:
        for k in feat.key_files:
            if not any(f == k.path or fnmatch.fnmatch(f, k.path) for f in on_branch):
                found.append({"kind": "missing-key-file", "feature": feat.slug, "path": k.path,
                              "detail": f"[{feat.slug}] key file {k.path} isn't on {branch} "
                                        f"(deleted, renamed, or not merged yet)"})
    return found


# --- what the agent gets ----------------------------------------------------------------

def describe(p: Project, todo: list[tuple[str, list[str]]], feats) -> str:
    blocks, used = [], 0
    for c, files in todo:
        head = git.lines(p.repo, "show", "-s", "--date=short", "--format=%h · %ad · %an — %s", c)
        owned = {f: sorted(owners(f, feats)) for f in files}
        by_feature: dict[str, list[str]] = {}
        for f, slugs in owned.items():
            for s in slugs:
                by_feature.setdefault(s, []).append(f)
        lines = [f"### {head[0] if head else c[:7]}"]
        for slug, fs in sorted(by_feature.items()):
            lines.append(f"- {slug}: {', '.join(fs)}")
        unclaimed = [f for f, slugs in owned.items() if not slugs]
        if unclaimed:
            lines.append(f"- no feature: {', '.join(unclaimed)}")
        stat = git.lines(p.repo, "show", "--format=", "--stat=120", c, "--", ".", *excludes())
        diff = git.lines(p.repo, "show", "--format=", "--no-color", "--no-ext-diff", c, "--", ".",
                         *excludes())
        if used < BATCH_DIFF_CHARS and diff:
            shown = diff[:DIFF_LINES]
            if len(diff) > DIFF_LINES:
                shown.append(f"... ({len(diff) - DIFF_LINES} more lines; read the files in the snapshot)")
            text = "\n".join(shown)
            used += len(text)
            lines += ["", "````diff", text, "````"]
        else:
            lines += ["", "\n".join(stat), "(diff left out to keep this short; read the files in the snapshot)"]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def touched(todo, feats) -> list[str]:
    seen = []
    for _, files in todo:
        for f in files:
            for s in sorted(owners(f, feats)):
                if s not in seen:
                    seen.append(s)
    return seen


def build_prompt(p: Project, todo, feats, branch: str, tip: str, snap: Path) -> str:
    from hippo.sync import feature_docs  # shared with the notes pass
    index = [l for l in features.render_index(feats).splitlines() if l.startswith("- ")]
    return prompts.render("catch-up.md", memory_dir=str(p.dir), snapshot=str(snap), branch=branch,
                          tip=tip[:7], hippo=str(paths.launcher()), features="\n".join(index),
                          feature_docs=feature_docs(p, touched(todo, feats)),
                          commits=describe(p, todo, feats))


def bash_prefixes() -> list[str]:
    h = paths.launcher()
    return [f"{h} feature set", f"{h} review add"]


def sandbox(p: Project, snap: Path) -> tuple[list[str], list[str]]:
    allow = ["Read", "Glob", "Grep", abs_rule("Edit", p.dir, "/features/**")]
    allow += [f"Bash({prefix} *)" for prefix in bash_prefixes()]
    deny = agent.secret_denies(p.repo) + agent.secret_denies(snap)
    deny += [abs_rule("Edit", p.dir, f"/{f}") for f in HIPPO_OWNED]
    deny += [abs_rule("Edit", p.repo, "/**"), abs_rule("Edit", snap, "/**")]
    return allow, deny


def snapshots_dir() -> Path:
    return paths.root() / "tmp"


@contextmanager
def snapshot(p: Project, tip: str):
    """The branch's files at `tip`, without secret files, in a folder deleted afterwards."""
    d = snapshots_dir() / f"{p.uuid}-{datetime.now():%Y%m%dT%H%M%S}"
    d.mkdir(parents=True)
    try:
        archive = subprocess.Popen(["git", "archive", "--format=tar", tip], cwd=p.repo,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        subprocess.run(["tar", "-xf", "-", "-C", str(d)], stdin=archive.stdout,
                       stderr=subprocess.DEVNULL, check=False)
        archive.stdout.close()
        archive.wait()
        for f in sorted(d.rglob("*"), reverse=True):
            rel = f.relative_to(d).as_posix()
            if f.is_dir() and not f.is_symlink() and f.name in SECRET_DIRS:
                shutil.rmtree(f, ignore_errors=True)
            elif (f.is_file() or f.is_symlink()) and is_secret(rel):
                f.unlink(missing_ok=True)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def clean_snapshots() -> None:
    """Leftovers from a sync that was killed."""
    shutil.rmtree(snapshots_dir(), ignore_errors=True)


# --- checks afterwards -----------------------------------------------------------------

def state(p: Project) -> tuple[set[str], dict[str, str]]:
    slugs = {f.parent.name for f in (p.dir / "features").glob("*/feature.json")}
    decisions = {f.parent.name: f.read_text() for f in (p.dir / "features").glob("*/decisions.json")}
    return slugs, decisions


def verify(p: Project, before) -> list[str]:
    problems = []
    slugs, decisions = state(p)
    _, unreadable = features.load_all(p.dir)
    problems += unreadable
    for s in sorted(slugs - before[0]):
        problems.append(f"feature '{s}' was created (only sessions declare new features)")
    for s in sorted(before[0] - slugs):
        problems.append(f"feature '{s}' was removed")
    if decisions != before[1]:
        problems.append("decisions changed (they only come from patch notes)")
    for s in slugs:
        for doc in ("overview.md", "technical.md"):
            if not (p.dir / "features" / s / doc).exists():
                problems.append(f"features/{s}/{doc} is missing")
    return problems


# --- the pass ---------------------------------------------------------------------------

def agent_options() -> dict:
    from hippo.sync import agent_options as shared  # same model and turn limit as the notes pass
    return shared()


def run(p: Project, backup, restore, log, say) -> bool:
    """Returns False if the agent's run failed (nothing moves; retried next sync)."""
    name = p.repo.name
    branch = git.default_branch(p.repo)
    tip = git.branch_tip(p.repo, branch)
    if tip is None:
        return True  # no commits yet
    commits, warning = commit_range(p, tip)
    if warning:
        say("warning", f"{name}: {warning}")
        log(name, p.uuid, f"warning: {warning}")
    todo = unexplained(p, commits)
    skipped = max(0, len(todo) - BATCH * MAX_BATCHES)
    if skipped:
        todo = todo[skipped:]  # keep the newest: the docs describe the present
        log(name, p.uuid, f"safety net: {skipped} older unexplained commit(s) skipped")
    feats = Memory(p.dir).features()[0]
    with_features = [(c, fs) for c, fs in todo if any(owners(f, feats) for f in fs)]

    for start in range(0, len(with_features), BATCH):
        batch = with_features[start:start + BATCH]
        say("sync", f"{name}: {len(batch)} commit(s) on {branch} without session notes; "
                    f"updating the feature docs")
        saved, before = backup(p), state(p)
        with snapshot(p, tip) as snap:
            allow, deny = sandbox(p, snap)
            result = agent.run(build_prompt(p, batch, feats, branch, tip, snap), cwd=p.dir,
                               add_dirs=[snap], tools=TOOLS, allow=allow, deny=deny,
                               bash_prefixes=bash_prefixes(), on_tool=agent.progress(p.dir, snap),
                               **agent_options())
        agent.record("catch-up", name, p.uuid, result, commits=len(batch))
        problems = verify(p, before) if result.ok else [f"catch-up agent: {result.error}"]
        if problems:
            restore(p, saved)
            say("failed", f"{name}: {problems[0]}{agent.stats(result)}")
            for line in problems[1:]:
                say("", f"! {line}")
            say("", "feature docs restored; these commits are retried next time")
            log(name, p.uuid, f"safety net failed: {'; '.join(problems)}")
            return False
        features.write_index(p.dir)
        say("updated", f"{name}: docs checked against {len(batch)} commit(s){agent.stats(result)}")
        feats = Memory(p.dir).features()[0]  # key files may have been fixed

    opened = review.reconcile(p.dir, findings(p, tip, branch, todo, feats),
                              checkable={"unclaimed", "missing-key-file"})
    meta = p.meta()
    meta["last_synced_commit"] = tip
    p.save_meta(meta)
    for marker in (p.dir / "unnoted").glob("*.json"):
        marker.unlink(missing_ok=True)

    covered = len(commits) - len(todo) - skipped
    summary = f"{len(commits)} new commit(s) on {branch}: {covered} covered by notes or nothing to document"
    if todo:
        summary += f", {len(todo)} without notes ({len(with_features)} touching features)"
    if skipped:
        summary += f", {skipped} older ones skipped"
    if opened:
        summary += f"; {len(opened)} new review item(s) (hippo open --review)"
    if commits or opened:
        say("ok", f"{name}: {summary}")
    log(name, p.uuid, f"safety net: {summary}")
    return True
