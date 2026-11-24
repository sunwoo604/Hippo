# Post-run secret scan

**What:** after any agent run (and on patch notes), scan everything written to
memory for secret-like patterns (private-key blocks, `AKIA…`, `sk-…`, `ghp_…`,
`xox…`); on a match, fail the run and quarantine the file.

**Why postponed (2026-10-05):** the init agent can't read secret files (deny
rules + the Bash guard hook, verified against the real CLI). A second layer is
cheap but not required for the MVP.
