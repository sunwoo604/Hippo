# Agent output validation and retry

**What:** after init or consolidation, validate everything the agent wrote and,
on failure, retry once with the errors fed back before reporting.

**Why postponed (2026-10-04):** the MVP validates structured data at write time
(`hippo feature set`, `hippo patch new`) and runs a minimal post-run check
(every feature has both docs). Full validation of prose docs plus automatic
retry can wait.
