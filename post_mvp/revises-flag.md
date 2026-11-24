# `--revises` for patch notes

**What:** `hippo patch new --revises <note>` so a later note can say "this
replaces part of note A", e.g. when the user changes logic they just built or
abandons an approach.

**Why postponed (2026-10-19):** patch notes stay append-only and consolidation
replays them in time order, so the newest note already wins for the current
state. An explicit link mainly helps when an approach is abandoned, so
consolidation knows note A's change no longer stands instead of inferring it.

**Notes:** a superseded decision is already marked `superseded`, not deleted.
`--revises` would let consolidation mark note A's lines as reverted and keep
the history of why.

## Update 2026-11-22: debugging back-and-forth (fix B, postponed)

**Problem:** in a fix → "still erroring" → fix loop, the Stop hook asks for a
note after every reply that changed code, so one bug could end up with
several notes, each recording an attempt rather than the final fix.

**Fix A (done 2026-11-22):** the note request tells Claude to write the note
only once the task is done or confirmed, and to reply "Still mid-task" while
debugging or waiting on the user. Not asked again until more code changes.

**Fix B (this item):** `hippo patch new --amend` replaces the note *this
session* already wrote, if it hasn't been synced yet, instead of adding a new
one. The session state already records when the session's notes were written
(`mark`), so the hook can tell Claude to amend when it's still the same task.
Result: one note per task, describing the final fix. Pairs with `--revises`
above (B covers the same session; `--revises` covers a later session).

**Why postponed:** fix A removes most extra notes; extra notes are harmless,
since consolidation replays them in order and the docs end up correct. Revisit
if notes still pile up in real use.
