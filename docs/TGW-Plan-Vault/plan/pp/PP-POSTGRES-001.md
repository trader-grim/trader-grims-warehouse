# PP-POSTGRES-001 — PostgreSQL item source-of-truth migration

**Opened:** 2026-07-13. **Status: PROPOSAL — design doc only, nothing built.**
Dave: "we have been futzing around with json for too long. Time to grow up.
Plus the ssd overheat today." Directly triggered by today's incident chain
(#1376/#1377/#1378, see below) plus a Perplexity research conversation Dave
ran separately (`inbox/RESEARCH-55K-ITEMDATA-POSTGRES-SOURCE-OF-TRUTH.md`)
confirming a hybrid design he'd already been leaning toward. Cross-links:
[[PP-DATAINTEGRITY-001]], [[PP-CATALOG-INCR-001]] (related but distinct —
see Relationship section below), [[PP-NIXOS-001]] (state_machine Postgres
instance already running), [[PP-AIOPS-001]] (NATS JetStream mutation stream
already partially built).

## Why now — the concrete case study

Same session, working an "eligible filter" web-UI bug, root-caused a chain
that is exactly the failure class this design eliminates:

1. **#1377**: the Eligible filter silently excluded any item with blank
   `status` — a data-shape bug a real schema constraint would have
   surfaced immediately (a `NOT NULL` column fails loud at write time; a
   missing JSON key fails silent at read time, forever).
2. **#1376**: two field names (`status` vs `#STATUS`) both meaningfully
   held "item status" for over a year with no schema to prevent the split.
   Dave confirmed `status` was always canonical; `#STATUS` was a manual
   convenience alias (sorted to the top of hand-inspected JSON) that was
   "sometimes not updated." Every write-path function in `items.py`
   (`statusupdate`, `verifiedupdate`, `bulk_edit`) has been writing to the
   wrong key this whole time — nothing caught it because nothing could.
3. **The strip incident**: `scripts/data_scrub_legacy_ebay_fields.py
   --apply` deleted `#STATUS` from 20,415 items in one uncommitted,
   unreviewable, unrollback-able pass on 2026-07-03 22:21. Reconstructing
   *what* changed and *when* took manually diffing two ItemArchive zip
   snapshots per SKU and grepping a stray report file I got lucky finding
   — there is no durable, queryable log of field-level changes anywhere
   in the system today.
4. **#1378**: the eBay sold-webhook handler has 500'd on every real call
   since 2026-06-04 — a dead code path with no test coverage, invisible
   for over a month. (Note: this specific failure mode is a testing gap,
   not a datastore gap — Postgres wouldn't have caught it. Listed here for
   completeness of the incident chain, not as a Postgres argument.)
5. **The thermal angle (Dave, same message)**: tgw-prod's recurring NVMe
   overheat problem is "almost always the SSD," and the item store today
   is 55k+ small JSON files plus per-write archive-zip creation plus
   recursive-scan catalog rebuilds — a write/IO pattern a WAL-based
   transactional engine handles far more efficiently than thousands of
   individual atomic-rename file operations. Not proven causal yet (see
   `TIGWA-EMERGENCY-tgw-prod-thermal-mitigation-20260713.md`'s own caution
   against declaring correlation without process-attribution evidence),
   but a real motivating data point, not just a code-quality one.

Of these, only #2 and the strip incident are actually Postgres-shaped
problems (schema + transactions + audit trail). #1 and #3 already got
one-off fixes; #4 is a testing gap. The point of this PP is that the
underlying pattern — no schema, no transactions, no durable change log —
is what let all of #2/#3 happen and go undetected for over a year, and
will keep producing this incident shape until fixed at the architecture
level, not the symptom level.

## The design (from Dave's Perplexity research, confirmed as his own design)

Hybrid authority model, not "database replaces filesystem":

- **PostgreSQL becomes the source of truth** for item identity, status,
  location, timestamps, workflow/pipeline state, and any field that gets
  filtered/searched/reported on — normalized columns for the hot fields,
  `jsonb` for content that's still evolving (draft_listing, item_specifics,
  identification_history, etc.) so nothing has to be fully flattened on
  day one.
- **Photos stay exactly where they are on disk**, unchanged, referenced by
  path/hash from a photo-metadata table. Dave, explicit: "I really don't
  like my photos in a database, and they aren't hit as hard as the data."
  No large-object storage, no BYTEA blobs — PostgreSQL's own docs and
  every practical source agree binary media belongs external.
- **JSON becomes a generated export artifact**, not the primary store —
  archive/disaster-recovery snapshots, portable interchange format,
  something diffable/greppable for manual inspection, produced FROM the
  DB rather than the DB being reconstructed FROM it. This is a real
  inversion of the current architecture (`invariants.md`'s "Raw is
  permanent; derived is recomputable" framing still applies, but which
  side raw vs. derived sits on flips).

Perplexity's proposed concrete shape (starting point, not final):
`items` (identity/status/location/timestamps/hot fields), `item_history`
(immutable audit trail — this is the piece that would have made today's
incident a 30-second query), `item_media` (photo metadata/paths/hashes,
FK to items), `item_analysis` (AI-derived structured content), `item_events`
(workflow/pipeline transitions).

## Role split: NATS JetStream is the bus, not the state master

Dave, 2026-07-13: "once you have a message bus like that use it. It just
isn't our state master." Explicit going forward: **PostgreSQL holds
current truth (queryable, transactional, the thing you read to know what
IS); NATS JetStream carries the durable event stream of what CHANGED
(audit trail, cache/projection invalidation, downstream consumers like
Tigwa/Hermes monitoring)** — a log, not a database. Every fence write
should publish its mutation to JetStream *in addition to* committing to
Postgres, never instead of. Don't let "we have a message bus" turn into
event-sourcing-as-primary-store (rebuilding state by replaying the whole
stream) — that's a different, heavier architecture this PP is not
proposing. Once the bus exists and is wired to the real fence (see next
section), use it broadly — it's cheap to add more consumers once the
publish side is correct, and doing so doesn't touch the state-master
question at all.

## What's already built and should be reused, not rebuilt

- **`state_machine` Postgres instance is already running** (todo #1351's
  own point, Dave 2026-07-12: "it is sitting there, might as well use
  it"). Today it only holds `queue_jobs` + `ai_usage`. Whether item truth
  lives in the same database/instance or a sibling one is an open question
  below, but the operational instance, backup story, and access pattern
  already exist — this is not a from-scratch infra stand-up.
- **NATS JetStream `ITEMDATA_MUTATIONS` stream + `publish_mutation()`
  already exist** (PP-AIOPS-001 Phase 1) — this is the exact transactional
  change-log mechanism that would have turned today's hour of archive-zip
  archaeology into a single query. **But it's wired to the wrong door**:
  only `items.py`'s narrow `_write_field()` CLI path calls it, not
  `http_server.py`'s `_apply_patch`/`_apply_ebay_write` — the actual fence
  choke point essentially all real traffic (worker patches, bulk edits,
  ebay-write, the strip script itself) goes through. **Recommend this
  becomes the very first packet, independent of and before the larger
  Postgres migration** — cheap, immediately valuable (every future
  incident like today's becomes a query), and doesn't require the schema
  design to be settled first.

## Relationship to PP-CATALOG-INCR-001 — needs reconciliation, not automatic supersession

PP-CATALOG-INCR-001 (opened 2026-07-03, still PROPOSAL, not built) explicitly
states as its Rule 1: **"One source of truth: ItemData (JSON + assets).
Never touch."** — it treats the SQLite catalog as a disposable projection
of JSON truth and proposes making that projection incremental instead of a
full rebuild-on-every-write.

This PP proposes the opposite premise: JSON stops being truth, Postgres
becomes truth. If this PP proceeds, PP-CATALOG-INCR-001's specific
problem (keeping a SQLite catalog projection in sync with JSON truth) may
simply stop existing — Postgres tables queried directly ARE the catalog,
no separate projection to keep in sync. But PP-CATALOG-INCR-001's CI-1
packet (wire `publish_mutation` into the real fence) is *exactly* the same
first step this PP also wants, for a different downstream reason (change
log vs. cache-sync trigger). **Recommend CI-1 gets built once, serving
both PPs**, and the rest of PP-CATALOG-INCR-001 (CI-2 through CI-5, the
SQLite incremental-upsert work) gets re-evaluated once this PP's schema
and migration path are settled — it may be fully absorbed, partially
still needed as a transition step, or genuinely obsolete. Not resolved
here; flagging so nobody builds CI-2 on JSON-is-truth assumptions while
this PP is live.

## Explicitly not decided yet — needs a dedicated planning pass

- **Migration path**: big-bang cutover vs. dual-write transition period
  vs. per-subsystem phased cutover (e.g., status/workflow fields first,
  draft_listing/AI content later). 55k+ existing items with live pipeline
  workers, eBay sync, and operator edits all touching the same records —
  this cannot be a weekend script.
- **Same Postgres instance as `state_machine`, or a sibling database?**
  Operational simplicity vs. blast-radius isolation (a bad migration
  against item truth is a different risk class than a queue-table bug).
- **Which fields get normalized columns vs. stay in `jsonb`** — the
  Perplexity conversation proposes "hot/filtered fields normalized,
  evolving content in jsonb," but the actual field list needs real design
  work against `TGW-Item-JSON-Schema.md`.
- **What "tgw-api is the fence" means after this** — today the fence
  reads/writes JSON files directly; after migration it reads/writes
  Postgres. The external contract (workers never construct paths, `{ok,
  ...}` responses, archive-before-overwrite semantics) should survive
  unchanged, but every internal implementation in `items.py` changes.
  This is a large but mechanical-in-spirit rewrite of the fence's guts.
- **JSON export cadence and format** — "occasional" per the research
  conversation; needs a concrete answer (per-write async export? nightly
  snapshot? on-demand only?) since some existing tooling (recoll indexing,
  `tgw-view-image.sh`, MC extfs scripts, manual grep/rsync workflows Dave
  explicitly values) currently depends on JSON files existing and being
  current.
- **Rollback/dual-read safety net** during transition — given today's
  incident was itself a botched one-way migration (JSON field stripped
  with no promotion step), this PP should not repeat that mistake at 100x
  the scale.
- **Reconciliation with PP-CATALOG-INCR-001** (see above) — Dave's call
  once this PP's shape is clearer.

## Recommended immediate next step (small, independent, low-risk)

Wire `publish_mutation()` into `http_server.py`'s `_apply_patch` and
`_apply_ebay_write` (same as PP-CATALOG-INCR-001's CI-1 packet) as a
standalone packet, before any schema/migration design work starts. This
closes the PP-AIOPS-001 Phase 1 coverage gap, gives an immediate durable
audit trail for every real fence write (not gated on the Postgres
migration being designed, let alone built), and directly prevents a repeat
of "reconstruct what changed by diffing archive zips by hand." Everything
else in this doc needs a dedicated planning session before any code moves.
