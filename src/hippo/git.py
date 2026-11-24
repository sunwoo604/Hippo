"""Thin wrappers over the git CLI. Every call is read-only."""

import re
import subprocess
from pathlib import Path

from hippo.errors import HippoError


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, stdin=subprocess.DEVNULL,
        capture_output=True, text=True,
    )


def _out(cwd: Path, *args: str) -> str | None:
    r = _git(cwd, *args)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def toplevel(cwd: Path) -> Path:
    root = _out(cwd, "rev-parse", "--show-toplevel")
    if root is None:
        raise HippoError(
            f"not inside a git repository: {cwd}",
            next_steps=["cd into your project's folder (or run `git init` there first).",
                        "Then run `hippo link` again."],
        )
    return Path(root).resolve()


def default_branch(repo: Path) -> str:
    """origin's HEAD if known, else main, else master, else the current branch."""
    remote_head = _out(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if remote_head:
        return remote_head.removeprefix("origin/")
    for name in ("main", "master"):
        if _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0:
            return name
    # Unborn HEAD (no commits yet) still has a symbolic branch name.
    return _out(repo, "symbolic-ref", "--short", "HEAD") or "main"


def commit_of(repo: Path, ref: str) -> str | None:
    return _out(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def remote_url(repo: Path) -> str | None:
    return _out(repo, "remote", "get-url", "origin")


def is_ignored(repo: Path, path: str) -> bool:
    return _git(repo, "check-ignore", "--quiet", path).returncode == 0


def normalize_remote(url: str) -> str:
    """Make SSH and HTTPS spellings of one repo compare equal.

    git@github.com:me/app.git, https://github.com/me/app, ssh://git@github.com/me/app.git
    all become github.com/me/app.
    """
    u = url.strip()
    u = re.sub(r"^[a-z+]+://", "", u)          # scheme
    u = re.sub(r"^[^@/]+@", "", u)              # user@
    u = re.sub(r"^([^/:]+):(?!\d+/)", r"\1/", u)  # scp-style host:path (not host:port)
    u = re.sub(r"\.git/?$", "", u.rstrip("/"))
    return u.lower()


# --- content and history (used by patch notes, hooks and the safety net) -------

DELETED = "0" * 40  # stands in for "this file doesn't exist" in blob maps
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def lines(repo: Path, *args: str) -> list[str]:
    r = _git(repo, *args)
    return [l for l in r.stdout.splitlines() if l] if r.returncode == 0 else []


def blob_hashes(repo: Path, rel_paths) -> dict[str, str]:
    """Git blob id of each file as it is now in the working tree (DELETED if gone)."""
    rel_paths = sorted(set(rel_paths))
    present = [p for p in rel_paths if (repo / p).is_file()]
    out = {p: DELETED for p in rel_paths if p not in present}
    if present:
        r = subprocess.run(["git", "hash-object", "--stdin-paths"], cwd=repo, input="\n".join(present),
                           capture_output=True, text=True)
        if r.returncode == 0:
            out.update(zip(present, r.stdout.split()))
    return out


def commit_blobs(repo: Path, commit: str) -> dict[str, str]:
    """Files a commit changed -> their blob id after it (DELETED for removals)."""
    r = _git(repo, "diff-tree", "-r", "-z", "--no-commit-id", "--root", commit)
    if r.returncode != 0:
        return {}
    parts = r.stdout.split("\0")
    out = {}
    for meta, path in zip(parts[0::2], parts[1::2]):
        fields = meta.split()
        if len(fields) >= 5:
            out[path] = fields[3]
    return out


def is_ancestor(repo: Path, old: str, new: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", old, new).returncode == 0


def branch_tip(repo: Path, branch: str) -> str | None:
    """Local branch or origin's copy, whichever is further ahead (local if they diverged)."""
    local = commit_of(repo, f"refs/heads/{branch}")
    remote = commit_of(repo, f"refs/remotes/origin/{branch}")
    if local and remote and local != remote and is_ancestor(repo, local, remote):
        return remote
    return local or remote


def tree_files(repo: Path, commit: str) -> list[str]:
    return lines(repo, "ls-tree", "-r", "--name-only", "--full-tree", commit)
