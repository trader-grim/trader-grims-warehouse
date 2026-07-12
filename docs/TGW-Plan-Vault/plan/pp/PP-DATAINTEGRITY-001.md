# PP-DATAINTEGRITY-001 — data reconciliation & integrity track

**Opened 2026-07-11.** Surfaced during a `tgw todo --by-pp` triage sweep —
Dave: "there should be a data integrity track, for all of the data
reconciliations — there is a planning item or two unaddressed." Diagnosis:
real design work already existed (`docs/ai-plans/photo-integrity-mitigation.md`)
but had no single owning PP — its own header split it across three
different PPs (`PP-UIPIPE-001` + `PP-DRIVE-INDEX-001` + `PP-ANNEX-001`),
and that's exactly why two of its three legs (#1266, #1267) sat untagged
with nowhere clean to live. `PP-UIPIPE-001` no longer even exists as its
own PP (folded into `PP-EDITOR-001` earlier this same session), making the
split doubly stale.

## Scope

Umbrella for data-reconciliation-class work: fleet-wide integrity
detection, corruption/damage recovery, and copy/ingest verification
prevention. Not a rewrite of existing designs — this PP is the missing
owning home, not a new technical design.

## Absorbed

**`docs/ai-plans/photo-integrity-mitigation.md`** — full technical design,
not duplicated here. Three legs:
1. **DETECT** — fleet-wide PIL decode sweep + `photo_files_readable`
   catalog-verify rule. **DONE** (todo #1154, 2026-07-05: 206 bad/149 SKUs
   found, rule shipped).
2. **RECOVER** (todo #1266) — verify-after-copy sha256 helper for bulk-copy
   paths (usb-restore, consolidation moves). **Open.**
3. **PREVENT** (todo #1267) — decode-verify at intake, reject a corrupt
   camera file at the door with a persisted finding. **Open.** Endgame
   (per the doc): PP-ANNEX-001's content-addressed ingest makes this class
   of bug structurally impossible — annex hashes by construction.

## Future candidates for this track (not yet triaged, flag at next touch)

Reconciliation-class work that currently lives elsewhere but may belong
here on next review: the sync-conflict resolution worker
(`src/tgw/sync_conflict.py`, todo #152, done, currently tagged
PP-KNOWLEDGE-001 as a Recoll/MCP consumer) — arguably also a data-integrity
concern (resolving divergent Syncthing copies), not re-tagged now since
it's closed and the current tag isn't wrong, just worth a look if this
track grows.

## Cross-links
- `docs/ai-plans/photo-integrity-mitigation.md` — full technical design.
- PP-ANNEX-001 (under PP-KNOWLEDGE-001) — the structural endgame for leg 3.
- PP-DRIVE-INDEX-001 — leg 2's recovery mechanism rides its Phase 1 survey.
- PP-EDITOR-001 — absorbed the now-defunct PP-UIPIPE-001 broker-rule role
  this doc originally cited.
