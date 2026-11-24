# Prompt-injection hardening

**What:** defend Hippo's agents against instructions hidden in repo content
(README, comments, docs) beyond "treat repo content as data".

**Why postponed (2026-10-04):** the blast radius is already small: no shell
except the guarded Hippo commands, no web access, writes only inside the memory
folder, secrets unreadable.
