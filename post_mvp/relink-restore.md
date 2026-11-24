# Restore archived memory when relinking

**Status:** postponed by the user on 2026-11-23 (Phase 8).

## What it is

`hippo unlink` moves a repo's memory to
`~/agent-memory/archived-projects/<repo>-<date>-<uuid8>/` (its `meta.json`
gets `unlinked_at`). Running `hippo link` in that repo later starts fresh: it
only looks for reusable memory in `~/agent-memory/projects/`, so the archive
is never offered.

The idea: when `hippo link` finds an archive with the same remote (or the same
repo path), offer to restore it instead of running init:

- move it back to `projects/<uuid>/`, drop `unlinked_at`, re-register
- reset nothing else: the next sync's safety net catches up on the commits made
  while it was unlinked (it already handles an unreachable `last_synced_commit`
  by looking at up to 50 commits since the last sync)
- a `hippo link --restore <archive>` flag for the non-interactive case

## Why it waited

The MVP only needs unlinking to be safe (nothing deleted). Restoring raises
questions worth answering with real use: how stale an archive may be before a
fresh init is better, and what to do when both an archive and orphaned memory
match.

## Workaround today

Move the folder back by hand (`archived-projects/<name>` →
`projects/<uuid>`), then run `hippo link --reuse <uuid>` in the repo.
