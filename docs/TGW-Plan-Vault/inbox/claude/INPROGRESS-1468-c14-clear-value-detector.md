# In progress: todo #1468 — C14 fleet-wide clear-value round-trip detector

Working in worktree `/opt/TGW/var/worktrees/1468-c14-clear-value-detector`
on branch `todo/1468-c14-clear-value-detector`.

Task: find every operator-facing save path in `src/tgw/http_server.py`
(and any other module that accepts operator-supplied field corrections),
and write a round-trip test per path proving a CLEARED value (not just a
changed one) actually persists on re-read. Per PP-LISTEDITOR-001 invariant
C14 (see `reference/invariants.md` C14 entry) — the Material incident
(2026-07-16) found this class of bug reactively on one path (aspects
form); this todo builds the general detector so future paths don't need
their own incident.

Plan: grep http_server.py for PATCH/POST endpoints accepting operator
field values, identify each save path, write round-trip tests under
tests/, run full offline suite, write result manifest + update C14 entry
in invariants.md with detector status.
