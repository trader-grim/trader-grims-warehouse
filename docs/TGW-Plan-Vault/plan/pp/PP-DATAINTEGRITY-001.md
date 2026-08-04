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

4. **Sold-order-history-gap wiring** (todo #1271) — `sold-order-history-gaps.jsonl`
   is written but had no reader wired in; absorbed into this track same
   session it was opened (invariant-C11 follow-up from commit 5c6223e).

## Future candidates for this track (not yet triaged, flag at next touch)

Reconciliation-class work that currently lives elsewhere but may belong
here on next review: the sync-conflict resolution worker
(`src/tgw/sync_conflict.py`, todo #152, done, never actually run against a
real conflict file, no systemd unit — currently tagged **PP-PORTABLE-CATALOG-001**,
corrected 2026-07-12 by Fable independent review #1338; this doc previously
misstated it as tagged PP-KNOWLEDGE-001) — arguably also a data-integrity
concern (resolving divergent Syncthing copies), not re-tagged now since
it's closed and the current tag isn't wrong, just worth a look if this
track grows.

## Cross-links
- `docs/ai-plans/photo-integrity-mitigation.md` — full technical design.
- PP-ANNEX-001 (under PP-KNOWLEDGE-001) — the structural endgame for leg 3.
- PP-DRIVE-INDEX-001 — leg 2's recovery mechanism rides its Phase 1 survey.
- PP-EDITOR-001 — absorbed the now-defunct PP-UIPIPE-001 broker-rule role
  this doc originally cited.

## Reconciled with a diverged duplicate copy, 2026-07-22 — STILL-LIVE BUG FOUND

A second, older copy of this file existed at `docs/TGW-Plan-Vault/pp/
PP-DATAINTEGRITY-001.md` (pre-migration location) carrying content this
canonical copy was missing entirely: **the `status` vs `#STATUS`
write-path bug (todo #1377's root cause, 2026-07-13).** #1377 itself is
closed (2026-07-14, tagged PP-COHESION-001) — but that todo only fixed
the narrow symptom (the web UI's Eligible filter silently excluding
blank-status items). **The deeper root cause was never fixed and is
confirmed still live right now** (checked `src/tgw/items.py` directly,
2026-07-22): `verifiedupdate()` still writes `doc['#STATUS'] = 'In
Stock'`, and `statusupdate()`'s own docstring literally reads "legacy
name; rename pending in data scrub pass 2" — the rename never happened.

**What this means concretely:** `status` (lowercase) was confirmed by
Dave (2026-07-13) as the real canonical field; `#STATUS` was a manual
convenience alias, "sometimes not updated." But `items.statusupdate()`,
`items.verifiedupdate()`, and `bulk_edit`'s status field
(`BULK_FIELD_KEYS['status'] = '#STATUS'`) have **always written to the
wrong key** — every operator status update via `tgw update-verified` or
the bulk editor has been silently landing on the stale/legacy field, not
the canonical one, this whole time, including today. As of the
2026-07-13 check: 5,118 items had neither key set (810 genuinely
unlisted/unsold, the rest already resolved via `ebay_listing`/
`ebay_offer`) — that count has not been re-checked since and is likely
stale in the other direction (more items affected by now, not fewer).

**Needs, none done yet:** (1) write-path fix — point `statusupdate()`/
`verifiedupdate()`/`bulk_edit` at `status`, stop writing `#STATUS`; (2)
`data_scrub_legacy_ebay_fields.py` either drops `#STATUS` from
`FIELDS_TO_CHECK` entirely or gets the same promotion-first guard already
built for legacy category fields (#1209/#1252); (3) `items.create_item()`
still has no default `status` for intake paths that omit it; (4) a real
reconciliation pass across all items with any status signal, once scoped.
Dave, 2026-07-13: "this is a big fix" — this PP is now that fix's owning
home, going forward, since it was never actually filed as a todo.

**Also preserved from the old copy**: the framing that this PP's own
reconciliation work (photo-integrity legs, this status bug) is Tigwa's
concrete starting scope for the knowledgebase buildout (PP-KNOWLEDGE-001)
— archive-snapshot diffing and write-path history tracing (exactly how
#1377's root cause was found: "ItemArchive snapshot diffs +
`data-scrub-1053-report.json`") is precisely the kind of search a mature
knowledgebase should make trivial instead of manual archaeology.

Old copy at `pp/PP-DATAINTEGRITY-001.md` renamed to
`pp/ARCHIVED-2026-07-22-PP-DATAINTEGRITY-001.md` (preserved, not
deleted). This file is now the sole canonical copy.
