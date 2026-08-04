# PP-DATAINTEGRITY-001 — data reconciliation & integrity track (full detail)

## PP-DATAINTEGRITY-001 — data reconciliation & integrity track — NEW 2026-07-11
**Dave: "there should be a data integrity track, for all of the data
reconciliations — there is a planning item or two unaddressed."** Correct
diagnosis — `docs/ai-plans/photo-integrity-mitigation.md` already existed
as a real 3-legged design (detect/recover/prevent) but had no single
owning PP (split across PP-UIPIPE-001+PP-DRIVE-INDEX-001+PP-ANNEX-001),
which is exactly why legs 2/3 (#1266, #1267) sat untagged with nowhere
clean to live — doubly true since PP-UIPIPE-001 no longer exists as its
own PP (folded into PP-EDITOR-001 same session). Leg 1 (detect,
`photo_files_readable` catalog-verify rule) DONE #1154 2026-07-05 — 206
bad/149 SKUs found. Legs 2 (verify-after-copy sha256 helper) and 3
(decode-verify at intake) open. Recovery still rides PP-DRIVE-INDEX-001
Phase 1; prevention's structural endgame still depends on PP-ANNEX-001.
Full design: `docs/ai-plans/photo-integrity-mitigation.md`; PP index:
`pp/PP-DATAINTEGRITY-001.md`.

**Target use cases for Tigwa's knowledgebase buildout (Dave, 2026-07-14):**
"I will have tigwa target these use cases in her knowledgebase build out."
This PP's own reconciliation work (photo integrity detect/recover/prevent
legs, the `status`/`#STATUS` write-path forensics below) is exactly the
shape of problem the git-annex/Recoll knowledgebase (PP-KNOWLEDGE-001,
buildout starting 2026-07-14) is meant to make fast — archive-snapshot
diffing, write-path history tracing, catalog-verify cross-checks were all
done by hand this project's history (see the `#1377` writeup below: found
via "ItemArchive snapshot diffs + `data-scrub-1053-report.json`," exactly
the kind of search a mature knowledgebase should make trivial). Concrete
starting scope for her, not abstract: legs 2/3 of the photo-integrity
design (open), and the `status`/`#STATUS` reconciliation pass (not yet
scoped/executed) once Dave scopes the "fun inventory."

**New leg 2026-07-13: `status` vs `#STATUS` write-path bug (todo #1377).**
Found while fixing the web UI's Eligible filter (it was silently excluding
items with blank status). Root-caused via ItemArchive snapshot diffs +
`/opt/TGW/var/log/data-scrub-1053-report.json`: on 2026-07-03 22:21,
`scripts/data_scrub_legacy_ebay_fields.py --apply` stripped the legacy
`#STATUS` key from 20,415 items, treating it like the script's other
genuinely-obsolete Magento artifact fields — but unlike its own sibling
guard for legacy category fields (which correctly refuses to delete until
the value is confirmed promoted to the canonical field first, #1209/#1252),
`#STATUS` had no equivalent protection. **Dave, 2026-07-13: `status`
(lowercase) was always the real canonical field — `#STATUS` was a manual
convenience alias (the `#` sorted it to the top of the JSON for hand
inspection) that was "sometimes not updated."** That inverts the obvious
read of the incident: the Jul 3 strip wasn't the core bug (removing a
stale convenience key is arguably correct), the core bug is that
`items.statusupdate()`, `items.verifiedupdate()`, and `bulk_edit`'s status
field (`BULK_FIELD_KEYS['status'] = '#STATUS'`) have **always written to
the wrong key** — every operator status update via `tgw update-verified`
or the bulk editor has been silently landing on the stale/legacy field,
never the canonical one. Live scope: 5,118 items currently have neither
key set (810 of those genuinely unlisted/unsold, the rest already resolved
via `ebay_listing`/`ebay_offer`). Dave: "this is a big fix" — logged only,
not yet scoped/executed. Needs: (1) write-path fix (point status writes at
`status`, stop writing `#STATUS`), (2) `data_scrub_legacy_ebay_fields.py`
either drops `#STATUS` from `FIELDS_TO_CHECK` entirely or gets the same
promotion-first guard as the category fields, (3) `items.create_item()`
still has no default status for intake paths that omit it, (4) real
reconciliation pass across all items with any status signal once Dave has
scoped the "fun inventory" — not attempted yet.

