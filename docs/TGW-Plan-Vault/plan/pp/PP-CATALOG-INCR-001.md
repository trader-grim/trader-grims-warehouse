# PP-CATALOG-INCR-001 — incremental catalog update (atomic per-item write + scheduled reconciliation)

**Opened:** 2026-07-03 (session 43, later same day as PP-PHOTOSYNC-001). Dave's
original design, recovered from `inbox/hermes-out-of-flake-portable-catalog-concept.md`
(lines 1273–1519, refined 1735–1880) — a Hermes/Perplexity planning transcript that
was sitting unprocessed in the inbox. **Status: PROPOSAL — not yet built.** Dave: "put
into a compiled plan proposal" after reviewing. The rest of that inbox file (Hermes
agent topology, git-annex/Recoll/JetStream knowledge plane, Hermes' document-triage
first project) is intentionally left unprocessed in the inbox per Dave's explicit
choice 2026-07-03 — a separate, larger planning session, not scoped here.

## The design, in Dave's words plus the refinement

"The thing to do is to atomically update the item json and the sqlite catalog and
thumbnail and then run a full catalog cleanup rebuild every so often. That was the
design." (Dave, 2026-07-03, in response to being shown `catalog_rebuild` doing a full
55,419-item disk scan + rebuild of all 4 artifacts on every single item write — 1,361
rebuilds in 33 hours, ~57s each, SQLite step alone 19.6s of that because it does
`DELETE FROM catalog` + full reinsert every time.)

The Hermes transcript (written for the portable-client case, but the principle is
identical for the server) refines this into three rules:
1. **One source of truth**: ItemData (JSON + assets). Never touch.
2. **The catalog is a disposable, rebuildable projection** — always allowed to
   regenerate from truth, never itself authoritative.
3. **Writes should update the projection incrementally at write time**; the full
   rebuild becomes a **periodic reconciliation pass**, not a per-write trigger — it
   exists to catch drift/orphans/anything the incremental path missed, not to be the
   normal update mechanism.

## What's already half-built (found while confirming the design against the code)

`tgw.apis.nats_client` already has an `ITEMDATA_MUTATIONS` JetStream stream and
`publish_mutation()` — exactly the event-signal mechanism this design needs (PP-AIOPS-001
Phase 1). **But it's only called from `items.py`'s `_write_field()`** (the single-field
CLI path, `tgw.items.update_item`) — **not** from `http_server.py`'s `_apply_patch` /
`_apply_ebay_write`, which is the actual fence choke point essentially all real traffic
(worker patches, bulk edits, ebay-write) goes through. The audit stream this design
would consume is pointed at the wrong door today.

## Proposed design (server side — the part that matters right now)

1. **Close the PP-AIOPS-001 Phase 1 gap first**: wire `publish_mutation` (or an
   equivalent per-write hook) into `_apply_patch` and `_apply_ebay_write` in
   `http_server.py` — the real fence, not just the CLI path.
2. **Atomic per-item SQLite upsert**: change `sqlite_catalog.build_sqlite_catalog`'s
   full `DELETE FROM catalog` + reinsert-all into an `INSERT ... ON CONFLICT(sku) DO
   UPDATE` for just the touched SKU(s), called synchronously (or near-synchronously,
   in-process) from the fence write path — no queue round-trip needed for a single
   SQLite row.
3. **Per-item thumbnail regen**: only enqueue `thumbnail_gen` for a SKU when the
   write actually touched the image/photo_order field — not on every write (it
   already exists as a separate worker; this is a triggering-condition fix, not new
   infra).
4. **`catalog_rebuild` becomes a timer job, not a per-write job**: stop calling
   `_enqueue_catalog_rebuild()` from every mutation site (14+ call sites in
   `http_server.py` alone, plus every pipeline worker). Replace with a systemd timer
   (e.g. hourly, or a few times/day — Dave's call) that runs the existing
   `build_all_catalogs()` unconditionally, as the reconciliation pass. Manual
   `tgw catalog-rebuild` trigger stays available for on-demand full rebuilds.
5. **`full_catalog`/`search_catalog` stay full-rebuild-only for now** — they're JSON
   arrays, harder to make safely incremental (array position, dedup, sort order) than
   a SQLite keyed table. SQLite is the whole win with none of that risk; revisit the
   JSON catalogs only if the timer cadence itself becomes a problem.

This directly revises the settled-architecture line "Catalog rebuild is always a
job" (master plan, "Settled architecture") to: **"Catalog rebuild is always a job —
now a scheduled reconciliation job, not a per-write trigger; per-write updates are
atomic and incremental (SQLite) or narrowly conditional (thumbnail)."** Needs Dave's
explicit sign-off before implementation since it changes a do-not-relitigate line.

## Itemized: what applies to CURRENT issue resolution (PP-PHOTOSYNC-001) vs. general

Dave asked specifically which part of this intersects with today's incident-fix work.
Short answer: **catalog_rebuild inefficiency did not cause or contribute to the EPS
quota exhaustion** (unrelated quota pool — local disk/CPU vs. eBay API budget) — this
is not part of the incident's root cause. But it IS an operational cost multiplier for
two packets already queued in the SAME track:

- **P4 (fleet photo repair, #1119)** — ~486 items ramped over an estimated 2-3 days.
  Every successful `ebay_upload`/`ebay_stage` completion currently enqueues a full
  `catalog_rebuild` (30s-coalesced, but P4's ramp is spread over days precisely to
  respect the EPS budget, so most individual repairs won't coalesce with each other —
  expect on the order of hundreds of additional full 57s rebuilds during the ramp if
  this isn't fixed first). **Recommend sequencing PP-CATALOG-INCR-001 step 2 (SQLite
  upsert) before P4's ramp starts**, purely to avoid multiplying today's CPU-cost
  finding by P4's own remediation work.
- **P8 (canary probe, #1124)** — runs daily, forever, by design. Each run presses
  real buttons and writes real data, triggering a real `catalog_rebuild`. Small in
  isolation (~1 extra full rebuild/day) but it's a permanent addition to the same
  cost category Dave flagged — worth having the incremental path in place before P8
  goes live so it doesn't quietly become "one more thing recreating the 1,361/33h
  pattern forever."
- **R1.8 (dataset snapshot, #1122)** — NOT applicable. Read-only capture, never
  writes ItemData, never triggers `catalog_rebuild`.
- **P1 (#1115, already done)** — NOT applicable going forward (already shipped); it
  did fire several real `catalog_rebuild` jobs during today's live-fire testing
  (visible in journalctl, e.g. `catalog rebuild triggered by: http_patch:...`),
  which is exactly the kind of everyday-edit trigger this proposal eliminates.
- **P2, P3, P5, P6, P7** — no meaningful interaction; they touch digest logic, tests,
  price guards, and the orphan queue, not high-volume ItemData writes.

**Recommended sequencing if Dave approves both tracks**: do PP-CATALOG-INCR-001 steps
1–2 (fence mutation hook + SQLite upsert) as a short packet BEFORE starting P4's ramp.
Steps 3–5 (thumbnail conditionality, timer cutover, JSON-catalog decision) can follow
independently — they're not blocking anything in PP-PHOTOSYNC-001, just general
efficiency. Nothing here blocks P1–P9 correctness in either direction.

## Packets (not yet filed as todos — proposal stage; file on Dave's go)

- **CI-1**: wire mutation publish into the real fence (`_apply_patch`,
  `_apply_ebay_write`) — closes PP-AIOPS-001 Phase 1's coverage gap. XS-S.
- **CI-2**: SQLite catalog upsert-on-write (replaces full delete+reinsert with
  per-SKU `INSERT ... ON CONFLICT DO UPDATE`, called from the fence write path).
  **Recommended before P4's ramp.** S.
- **CI-3**: thumbnail regen triggering condition (only on image/photo_order change,
  not every write). XS.
- **CI-4**: `catalog_rebuild` cutover to a systemd timer; remove/no-op the 14+
  per-write `_enqueue_catalog_rebuild()` call sites (or repoint them at the cheap
  per-item path from CI-2/CI-3); keep manual full-rebuild trigger. S-M — touches many
  call sites, needs a careful test pass since several tests assert catalog_rebuild
  gets enqueued on specific actions.
- **CI-5**: decide full_catalog/search_catalog fate (stay full-rebuild-only vs.
  future incremental) — a decision packet, not a build packet, deferred until CI-1–4
  prove out.

## Decisions (Dave, 2026-07-18 — greenlit, resolving both open questions)

- **Reconciliation timer cadence: hourly.** Full 4-artifact rebuild via systemd timer,
  ~24/day vs. today's ~1,361/33h (~990/day) pattern — over 40x reduction, plus the
  incremental path means the hourly rebuild is now purely a drift/orphan catch-all,
  not the primary update mechanism.
- **CI-2's SQLite upsert runs synchronously**, inside the same HTTP request as the
  write, per the design doc's own recommendation — matches Dave's original
  "atomically" wording; sub-millisecond single-row upsert cost, no staleness window.

Todos filed 2026-07-18: #1548 (CI-1), #1549 (CI-2), #1550 (CI-3), #1551 (CI-4).
CI-5 stays deferred (decision packet, not build packet, per the doc — revisit once
CI-1–4 prove out).
