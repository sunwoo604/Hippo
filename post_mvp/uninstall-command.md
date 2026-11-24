# `hippo uninstall`

**What:** undo `hippo install`: remove the launcher and `~/.local/bin/hippo`,
the scheduled job, Hippo's entries in each linked repo's
`.claude/settings.local.json`, and `.hippo` files; keep `~/agent-memory/`
unless `--purge` (with a typed confirmation).

**Why postponed (2026-10-01):** not required for the MVP or for a plugin
marketplace (`/plugin uninstall` removes the plugin itself). Phase 7 makes the
daily job do nothing once Hippo is gone, so leftovers are harmless.
