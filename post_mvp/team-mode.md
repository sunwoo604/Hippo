# Team mode

**What (from the spec):** the memory dir becomes its own git repo (or moves
into the project repo), `.hippo` gets committed, Hippo's hooks move into each
repo's committed `.claude/settings.json` so teammates get them without
installing, consolidation runs in CI on merges to main and opens a PR for
human review. Patches stay one file per session to avoid merge conflicts.

**Related:** split machine-specific files (config.json, bin/, lib/, logs/,
state/, backups/) from shared memory data (projects/), e.g. XDG-style
locations, so only memory is shared.

**Already prepared in the MVP:** `author` in patch notes; per-repo hooks;
plain files throughout.
