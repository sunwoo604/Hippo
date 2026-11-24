# Prompt additions

**What:** let users add their own instructions to Hippo's built-in agent prompts
without replacing them, e.g. `~/agent-memory/prompts/init.extra.md` (all
projects) and a per-project file in the memory folder, appended to the
built-in prompt at run time.

**Why postponed (2026-10-06):** prompts now ship only inside the package,
because they are coupled to the code (commands, sandbox rules, formats) and a
stale user copy broke init. Additions are the safe way to customise, but not
needed for the MVP.

**Notes:** examples of what users want to tune: fewer/more features, a name
other than `platform`, house style for docs. Additions must never be able to
change the commands or formats the code relies on.
