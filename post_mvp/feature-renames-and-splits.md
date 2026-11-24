# Feature renames and splits

**What:** let a session (or the user) rename a feature or split one into two,
with consolidation moving docs, key files and decisions accordingly.

**Why postponed (2026-10-17):** slugs are referenced by every patch note and
decision; changing them means moving decisions and rewriting history, too risky
to automate in the MVP.

**MVP behaviour:** `hippo patch new` refuses it; the session agent notes it in
Follow-ups and consolidation adds an item to `needs-review.md` for the user.
