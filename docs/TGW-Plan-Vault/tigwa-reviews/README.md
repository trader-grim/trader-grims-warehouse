# Tigwa's plan-review output

**Ownership:** write-exclusive to Tigwa's `tigwa-canonical-plan-review`
Hermes job. Nothing else writes here. Everything else in the Plan Vault
stays read-only to that job.

**Direction:** one-way, Tigwa → this folder → Syncthing → Dave's devices.
Consumer-side edits (Dave/Claude editing files in this folder directly)
are errors, not collaboration — corrections to Tigwa's judgment go back
through the inbox seam as a normal note, not by editing her output.

## Files

- `latest.md` — always the current review, atomically overwritten
  (write-temp-then-rename, so Syncthing never sees a partial file).
- `YYYYMMDD-HHMM-plan-review.md` — only written for a *substantive* review
  (something actually changed since the last one). A no-change run updates
  only a checked-time marker in `latest.md`, no new file, no sync churn.
- `state.json` stays local to a1131 (Tigwa's own mechanical comparison
  state) — not published here, nothing in this folder needs it.

## Review content contract (per Tigwa's own proposal, adopted as-is)

Each substantive review separates:
1. Source evidence (checked time, repo HEAD, uncommitted status, hashes,
   changed files).
2. Observed facts (what the canonical source actually says).
3. Tigwa's interpretation/judgment (implications, priorities,
   contradictions, confidence).
4. Unknowns and risks.
5. Next attention points.
6. Corrections/feedback status (provenance of any prior correction).

## Retention

No automatic pruning for now — these are small text files, Syncthing
already handles distribution/version retention on the sync side. Revisit
if the folder grows large enough to matter.

## Not part of canonical plan

This folder is Tigwa's own observation/judgment output, not a canonical
plan amendment — `tgw plan check`/`tgw plan status` and the master plan's
own structure are unaffected by anything published here. Don't let future
tooling treat this folder as authoritative plan content.
