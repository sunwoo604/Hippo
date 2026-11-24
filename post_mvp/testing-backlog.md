# Testing backlog

Real-use scenarios not yet exercised (as of 2026-10-19):

- Plugin route through a real marketplace install (`/plugin marketplace add`
  + `/plugin install`), not just `claude --plugin-dir`.
- A long session with several tasks: one note per task, no nagging.
- Claude answering "nothing meaningful changed" to the Stop hook.
- SessionEnd when a session is closed from the desktop app (the first trial
  left its state file behind; see Phase 5 notes).
- Safety net on a real repo: commits made in the terminal after a session's
  note (should count as explained), a GitHub squash merge, a force-push that
  makes `last_synced_commit` unreachable, and origin ahead of local main.
- `noted-files.json` grows with every note and is never pruned; check its size
  after a few months of use (prune entries for content older than
  `last_synced_commit` if needed).
