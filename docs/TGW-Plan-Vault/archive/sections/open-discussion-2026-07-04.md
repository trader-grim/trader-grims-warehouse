# Open discussion items (for 2pm 2026-07-04 planning session) — archived, session long past

## Open discussion items (for 2pm 2026-07-04 planning session)

**Web UI vs Flutter app — MOVED to `pp/PP-UIUX-001.md`, 2026-07-16.** This
whole discussion (the 2026-07-06 investigation, the 2026-07-11 nuance pass,
the three undecided directions) sat under this stale, dateless heading for
ten days with no PP assigned — exactly the kind of orphan `tgw-plan-
maintain` exists to catch. Dave, 2026-07-16, gave it a real home: "flutter
vs web is in with the ui inventory/tgw mapping/ui ux project. plan is to
fully define then have entire set including web ui and flutter to the spec
by ui/ux specialist coder." Full content preserved verbatim at
`pp/PP-UIUX-001.md` — see that doc, not here, going forward.

**Relocate the plan-vault document inbox into `/opt/TGW/incoming/`?** Dave recalled
discussing this before (2026-07-04) but no record of it was found in this plan, any
PP design doc, or memory — capturing now per Prime Directive 5 so it isn't lost
again. `/opt/TGW/incoming/` was built session 42 as the general "root of ALL inbound
data" (Data Charter) — `newitems/` (camera/intake drops), `ebay/` (raw API capture,
E7), `lookups/` (reserved) — but `docs/TGW-Plan-Vault/inbox/` (research docs,
PP-intake notes, Syncthing-synced across workstations) remains separate, its own
thing. Open question: should the vault inbox move under `/opt/TGW/incoming/`
alongside the other inbound streams, or does it stay separate since it's
document/note intake (human research, Syncthing-native) rather than raw
API/photo capture (machine-written, group-only perms, different retention model)?
Dave is linking his existing `docs/TGW-Plan-Vault/inbox/` via Syncthing across his
workstations in the meantime — no filesystem move happening until this is decided.

**Self-healing philosophy is visibly working — verify quality at 2pm (Dave, 2026-07-03).**
Observed live tonight during the overnight queue: the agent is finding, investigating,
and logging anomalies as part of the normal workflow, not just executing tasks blind —
e.g. the R1.8 snapshot's per-SKU error counter jumping 3→23→29 was checked against the
quota-incident log and confirmed benign (silently-counted 404s for items with no offer,
not 429s/quota exhaustion) before being written off, rather than either ignored or
mis-flagged as an alarm. Matches the standing design philosophy (memory:
feedback-self-healing-system — auto-detect, auto-sanitize or surface, self-service
resolution, never just patch-and-move-on). Dave wants this specifically verified for
quality at the 2pm session — i.e. confirm the investigations are actually correct and
thorough, not just reassuringly-worded, before trusting the pattern going forward.

**PP-INTAKE-004 — PROMOTED to active PP 2026-07-11**, no longer just a
discussion item — see its own heading above (Pending projects index) and
full design `pp/PP-INTAKE-004.md`. The platform-question half (is TGW a
sellable platform) remains genuinely open and is now explicitly
acknowledged-and-parked rather than an unstructured loose end — three
business models named (multi-tenant host / licensed self-host / open-core
services), not chosen between, flagged as its own future planning topic.
Also still open, unsolved: "Tasker Permissions" companion-app absorption
(todo #1227, revisit when this track thaws); `clip-route`'s capture-before-
SKU-exists correlation ID (lands in PP-INTAKE-004's Phase 2).

**catalog_rebuild dead-letter root cause (2026-07-04): SKU-rename races, not a bug.**
15 `catalog_rebuild` dead-letters, all "No such file: ItemData/<old-sku>/..." —
confirmed via `sku_history`: each old SKU was renamed by `ebay_sku_migrate`
(e.g. `tgw20171218042138799` → `tgw201712180421387`, `normalize_class_a`,
2026-06-29) and the new SKU directory exists fine. A rebuild scan just caught
the old path mid-rename; catalog rebuilds have clearly succeeded since (fresh
catalog data used all night). Cancelled (Dave's go). Not fixed at the source —
worth a small robustness pass later (`_verify_item`/`build_all_catalogs`
tolerating a missing file mid-scan as a skip-and-continue rather than failing
the whole rebuild) if this recurs during a future migration batch.

- DRAFT-1076-eps-support-ticket.md filed — pending Dave's review/submit.

2026-07-10 planning session agenda filed as reference (see reference/AGENDA-planning-session-2026-07-10.md).
