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

## Phased build plan — Dave, 2026-07-22: "plan both. This is our
## opportunity for those big projects. This is the justification."

Tonight's confirmed-live `#1377` bypass (`items.py`'s `verifiedupdate()`
hand-rolling its own write instead of calling `_write_field()`) is the
concrete case this migration exists to make structurally impossible, not
just harder. Sequenced so each phase ships independent value and none
requires the next to already exist:

1. **Phase 0 (packet-ready today)** — the "Recommended immediate next
   step" above: wire `publish_mutation()` into `http_server.py`'s real
   fence. Zero schema risk, immediate audit-trail value, works whether or
   not the rest of this migration ever happens.
2. **Phase 1 — schema + dual-write.** Design the normalized-columns-vs-
   jsonb split (against `TGW-Item-JSON-Schema.md`, not from the Perplexity
   conversation's guess), stand up the Postgres tables (same instance as
   `state_machine` unless Phase 1's own design work concludes isolation is
   worth the operational cost — still an open question above, resolve
   here not before). `_write_field()`/`update_item()` write to BOTH
   Postgres and the existing JSON files during this phase — Postgres is
   not yet authoritative, this is the rollback/dual-read safety net the
   "explicitly not decided yet" section above calls for, built in from the
   start rather than bolted on after a bad one-way migration (which is
   exactly how today's `#1377`-class incidents keep happening).
3. **Phase 2 — backfill + verify.** Migrate all 55k+ existing items into
   the new schema, dual-read verification (Postgres value vs. JSON value
   must agree for every migrated item before cutover) — same spirit as
   invariant C12's migration note (old and new shapes coexist correctly
   for as long as the transition takes, not a flag day).
4. **Phase 3 — cutover: Postgres becomes authoritative, JSON becomes
   export.** `_write_field()` writes to Postgres only; JSON export job
   (cadence TBD per the open question above) generates the files existing
   tooling (recoll, `tgw-view-image.sh`, MC extfs, manual grep/rsync
   workflows) still depends on. This is the phase that actually removes
   today's `atomic_write_json`-anywhere bypass surface, by construction —
   there's no longer a live JSON write path for a bug to reimplement.
5. **Phase 4 — the actual unbypassable fence: column-level `GRANT`/
   `REVOKE`.** The concrete requirement from tonight's discussion: the
   application's default DB role gets no `UPDATE` privilege on canonical
   status/location/workflow-state columns; only a dedicated fence
   role/function (`_write_field()`'s Postgres-side equivalent) can write
   them. This is the phase that makes bypass not just "structurally
   inconvenient" (Phase 3) but literally refused by the database engine
   regardless of how a future bug tries to construct the write — the
   actual "fence that cannot be crossed." Sequenced last because it's the
   least useful without Phases 1-3 already making Postgres the real
   target worth protecting.

Each phase is independently valuable and independently stoppable — this
is the justification for treating PP-POSTGRES-001 as real, sequenced work
rather than a someday-proposal, not a commitment to build all 5 phases in
one push.

## Sequencing supersedes: Tigwa's 3-lane capacity model, 2026-07-22

Tigwa (DECISION/RESPONSE/REVIEW docs, 2026-07-22, folded into
`TGW-Master-Plan.md`) re-derived the phase list into P0-P6 with two extra
evidence gates (a shadow-database proof stage before dual-write; an
explicit backup/restore-drill gate before cutover) and reframed this PP as
a capacity-funded third lane, never blocking the critical-integrity or
product/harness runways. **Dave, same day: "We discussed it, she both
agrees with me and I agree with her. We are going to postgresql. But this
is what we have and we need to plan the migration."** — direction
authorized, three-lane model settled. Cutover (P5) still needs Dave's
explicit go/no-go later; this section is the P1 work that follows from
today's authorization.

## P1 — Migration Contract (drafted 2026-07-22, in response to today's authorization)

Six deliverables Tigwa's P1 stage calls for. Grounded in real numbers
pulled live from tgw-prod today, not estimates.

### Baseline measurements (live, 2026-07-22)

- **55,421 items** in `ItemData/` (`ls | wc -l`).
- **181 GB total** `ItemData/` tree — overwhelmingly photos; JSON itself is
  small (see below). No baseline yet for photo-vs-JSON split by bytes —
  worth a follow-up `du` pass excluding `*.json` before P2, not blocking.
- **JSON file size** (2,000-file sample): mean 7.5 KB, median 3.0 KB, max
  41.6 KB. Confirms `jsonb` is the right shape for the evolving/nested
  content (`draft_listing`, `item_attributes`, `ebay_offer` etc.) — even
  the max case is trivially small for Postgres, no TOAST-pressure concern.
- **`state_machine` DB already exists and is small**: 464 MB total,
  `queue_jobs` alone holds 310,899 rows. Tables already present:
  `queue_jobs`, `queue_job_history`, `queue_workers`, `agent_runs`,
  `ai_usage`, `image_hashes`, `sku_history`, `todo_items` — i.e. this
  instance already carries several item-adjacent concerns (`sku_history`,
  `image_hashes`), not just the queue.
- **Write pattern not yet measured**: items/day intake rate, mutation
  fence call volume (once P0 lands and `publish_mutation()` sees real
  fence traffic, `ITEMDATA_MUTATIONS` stream depth becomes a live proxy
  for this) — defer to after P0 ships, don't estimate blind.

### Schema decision — normalized hot fields + jsonb, grounded in the real field list

Cross-referenced against `reference/TGW-Item-JSON-Schema.md` (not the
original Perplexity guess). Proposed split:

**Normalized columns** (`items` table) — fields that are filtered, joined,
or indexed on today: `sku` (PK, `tgwYYYYMMDDHHMMSSmmm`), `title`,
`location`, `#STATUS` → `status`, `#VERIFIED` → `verified`, `category`,
`condition`, `ebay_category_id`, `ai_identified` (bool),
`draft_listing_state`, `baseline_at`, `source_sku`, `legacy_listing_resolved`
(bool), `reprice_skip` (bool), plus denormalized hot lookups pulled up
from nested dicts because they're queried directly today:
`ebay_offer_status`, `ebay_offer_price`, `ebay_listing_id`,
`ebay_listing_status`, `ebay_listing_live_price`. These map 1:1 to
existing filter/search/report code paths (`tgw search`, catalog rebuild,
the eligible-filter bug class from #1377/#1376 this PP was originally
opened to fix).

**`jsonb` columns** — everything else, one column per top-level envelope
so each keeps its own update/read pattern and doesn't force a full-row
rewrite on unrelated changes: `product_lookup`, `draft_listing` (whole
dict, including `item_specifics` Set B — already self-describing per
invariant C12's envelope shape), `item_attributes` (whole dict, Set A,
same envelope shape), `ebay_offer`, `ebay_listing`, `ebay_photos`,
`reprice_schedule`, `price_history`, `pipeline_error`, legacy-only fields
(kept as a single `legacy_fields jsonb` bucket, read-only, not migrated
forward).

**Separate tables, not embedded jsonb** (append-only history, same "cheap
current value + history array" shape already in the JSON — Postgres rows
are a strictly better fit than a growing array inside one document):
`item_attributes_history`, `item_specifics_history`, `price_history`
already qualifies structurally but is small/low-write enough that
`jsonb` array is fine to keep for now — revisit only if P1 baseline
shows otherwise.

**Not resolved here, explicitly deferred to P2 (shadow import) design:**
exact column types/constraints, index list, whether `item_attributes`/
`item_specifics` envelope versioning needs a Postgres-side check
constraint mirroring the `_set` marker.

### Same-instance vs. sibling database

**Recommend: same `state_machine` instance, new schema (`items`
schema, not `public`), not a sibling database.** Reasoning: `state_machine`
already holds `sku_history` and `image_hashes` — item-adjacent data is
already there, a sibling DB would just relocate part of an already-split
concern. Operational simplicity (one backup/restore/WAL story, already
running per todo #1351) outweighs blast-radius isolation at this dataset
size (464 MB current DB, item data adds low-single-digit GB even fully
normalized — nowhere near a scale where instance separation buys real
isolation). **Named risk Tigwa's guardrails require flagging**: a bad
migration against item truth is a different risk class than a queue-table
bug — mitigated by schema separation (`items.*` vs `public.*`) plus the
P1 backup contract below, not instance separation. Revisit if P1 baseline
work finds a write-volume or lock-contention reason this doesn't hold.

### Data-product inventory (what could move to a DB projection at P4)

Consumers that scan/filter/aggregate ItemData today, in rough order of
how directly they'd benefit from indexed queries: catalog rebuild
(`build_all_catalogs`, full-scan every trigger — the single biggest
win), `tgw search` / eligible-filter UI paths (#1377's bug class),
reporting/velocity_stats worker, Radar (per PP-CATIONIX-001, not yet
built — design it against the DB projection from day one rather than a
filesystem scan), any future workflow/handoff view Tigwa's agents need.
None of these are P4 commitments yet — this is the inventory Tigwa's P1
asked for, not a build order.

### Backup contract (P1 deliverable, gates P5 per Tigwa's non-negotiable #1)

**Checked live, 2026-07-22 — better starting position than assumed.**
`tgw-db-backup.service`/`.timer` (PP-BACKUP-001 A1) already runs a daily
`pg_dump` of `state_machine`, last successful run confirmed 15h before
this check (`systemctl status`: `Deactivated successfully`, exit 0). So
the "does `state_machine` have any backup today" question is answered:
yes, daily logical dump, already running, already covers the instance
this PP proposes using (same-instance decision above). **Still missing,
per `PLAN-backup-dr.md`'s own A5 spec** (procedure exists, execution
status unconfirmed by this check): the actual restore drill — `createdb
scratch && pg_restore -d scratch <newest.dump>` with row-count
verification, and a recorded wall-clock RTO number. Also not yet
resolved: WAL/PITR-level continuous backup (current mechanism is a daily
full dump, i.e. up-to-24h RPO, not point-in-time) and off-host copy
confirmation for the dump destination. **P1 is NOT complete until an A5
drill is actually run and its result (pass/fail, timing) is recorded here**
— Tigwa's non-negotiable #1 explicitly forbids assuming DB backup is
better than JSON's until proven, and a spec'd-but-unexecuted drill doesn't
satisfy that.

**A5 drill executed live, 2026-07-22 — PASS.** `sudo -u postgres createdb
-O tgw scratch_restore_drill` + `pg_restore -U tgw -d scratch_restore_drill
--no-owner --no-privileges state_machine-20260721.dump` (newest dump,
58.5MB). Zero errors. Wall-clock: createdb under 1s, restore 11s, total
11s. Row-count comparison against live `state_machine` for all 8 tables:
every delta traced to normal activity in the roughly 15h between dump and
check (e.g. `todo_items` +26 matches exactly the todos filed this session,
`queue_jobs` +107 is normal queue churn) — no unexplained mismatch.
Scratch DB dropped after verification, no residue left on the host.
**Corrected 2026-07-22 (Tigwa calibration, verified live via `tgw
health`)**: the drill proves the dump/restore *mechanism* works, but do
not read that as "the backup contract is done." `tgw health`'s `backups`
check is a real pre-existing WARN, checked live: `rclone sync never
completed (stamp absent)`, `snapshot tree stale: 16.2h since last entry
(limit 1h)`, `no encrypted secrets bundle found in
/opt/TGW/mnt/tgw-db-backup/.../secrets`. This is different infrastructure
than what the A5 drill tested — the drill covers the `pg_dump` +
same-script `rsync` to the second physical drive (`tgw-db-backup.timer`),
the health-check failures are about a separate `rclone` off-host sync leg
and a secrets-bundle encryption leg that were never wired up or have
stalled. **Distinguish configured automation from verified off-host
recoverability** (Tigwa's exact framing): the daily-dump-plus-local-rsync
mechanism is proven; genuine off-host (beyond the second local drive) and
encrypted-secrets-bundle recoverability are NOT proven and are real open
gaps, not paperwork.

**Still open**: WAL/PITR continuous backup (current RPO is about 24h,
daily full dump only), the `rclone` off-host sync leg (currently never
completing), the encrypted secrets bundle (currently absent), and
snapshot-tree freshness (16.2h stale against a 1h limit — separate from
the DB dump, likely the `ItemData` snapshot mechanism, not yet traced).
**P1's backup-contract gate is satisfied only for the daily-dump +
local-second-drive mechanism** — a future PITR/RPO improvement is an
enhancement, not a P1 blocker, but the rclone/secrets-bundle/snapshot-
freshness gaps are pre-existing `tgw health` findings that should get
their own todo, separate from this PP — they predate and are independent
of the Postgres migration question, the A5 drill just happened to surface
them while checking the specific mechanism P1 depends on.

### Rollback / provenance / conflict semantics

Carries forward unchanged from the original "explicitly not decided yet"
section below, now scoped concretely: every dual-write-era row (P3)
carries a `source_of_truth` marker or equivalent so a partial/aborted
migration is never ambiguous about which side is authoritative for that
record; every migrated row's `item_attributes_history`/
`item_specifics_history` provenance carries forward unchanged (append-only,
never rewritten) rather than being flattened/summarized during import —
same "raw is permanent" rule as everywhere else in this project. Conflict
behavior for P3's dual-write pilot (JSON write succeeds, Postgres write
fails, or vice versa) needs a concrete retry/reconcile procedure — not
designed yet, flagged for P3's own packet, not P1.

## Technical deep-dive: exact schema, index design, dual-write mechanics (2026-07-22)

Per Dave's direction to unfold this PP rather than keep it compressed —
this is design-only, nothing built. Grounded directly in the real write
paths (`items.py`'s `_write_field`/`set_fields`, `http_server.py`'s
`_apply_patch`/`_apply_ebay_write`) and `reference/TGW-Item-JSON-Schema.md`,
recalibrated per Tigwa's 2026-07-22 note: P1 stays a short evidence pass;
this section is the *design thinking* that pass draws on, not new P1 scope
by itself.

### Exact schema (DDL, P2's actual target — not built yet)

```sql
CREATE SCHEMA IF NOT EXISTS items;  -- separate from public, per the
                                     -- same-instance blast-radius decision above

CREATE TABLE items.items (
    sku                       TEXT PRIMARY KEY,          -- tgwYYYYMMDDHHMMSSmmm
    title                     TEXT NOT NULL DEFAULT '',
    location                  TEXT,
    status                    TEXT NOT NULL DEFAULT 'new',   -- was #STATUS
    verified                  TEXT,                          -- was #VERIFIED
    category                  TEXT,
    condition                 TEXT,
    ebay_category_id          TEXT,
    ai_identified             BOOLEAN NOT NULL DEFAULT FALSE,
    draft_listing_state       TEXT,
    baseline_at               TIMESTAMPTZ,
    source_sku                TEXT,
    legacy_listing_resolved   BOOLEAN NOT NULL DEFAULT FALSE,
    reprice_skip              BOOLEAN NOT NULL DEFAULT FALSE,

    -- denormalized hot lookups already queried directly today
    ebay_offer_status         TEXT,
    ebay_offer_price          NUMERIC(10,2),
    ebay_listing_id           TEXT,
    ebay_listing_status       TEXT,
    ebay_listing_live_price   NUMERIC(10,2),

    -- jsonb envelopes, one column per top-level dict, own update pattern
    product_lookup            JSONB,
    draft_listing              JSONB,   -- includes item_specifics (Set B)
    item_attributes            JSONB,   -- Set A envelope, invariant C12
    ebay_offer                 JSONB,
    ebay_listing                JSONB,
    ebay_photos                 JSONB,
    reprice_schedule             JSONB,
    price_history                 JSONB,
    pipeline_error                 JSONB,
    legacy_fields                    JSONB,  -- read-only, not migrated forward

    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_of_truth            TEXT NOT NULL DEFAULT 'json'
        CHECK (source_of_truth IN ('json', 'postgres', 'both'))  -- P3 marker
);

-- Append-only history — same "cheap current value + history array" shape
-- the JSON already uses, moved to real rows (invariant C12's provenance).
CREATE TABLE items.item_attributes_history (
    id             BIGSERIAL PRIMARY KEY,
    sku            TEXT NOT NULL REFERENCES items.items(sku),
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    key            TEXT NOT NULL,
    value          JSONB,
    previous_value JSONB,
    source         TEXT,
    applied_by     TEXT
);
CREATE TABLE items.item_specifics_history (LIKE items.item_attributes_history INCLUDING ALL);

-- P3's outbox (see Dual-write mechanics below) — not part of P2's shadow import.
CREATE TABLE items.mutation_outbox (
    id          BIGSERIAL PRIMARY KEY,
    sku         TEXT NOT NULL,
    field       TEXT NOT NULL,
    old_value   JSONB,
    new_value   JSONB,
    source      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at  TIMESTAMPTZ
);
```

**Not resolved here, explicitly deferred to P2's own live population**:
exact `NUMERIC` precision for price fields against real data (some
existing prices may need more than 2 decimal places — check before
committing the type), whether `condition`/`category` should be Postgres
`ENUM` types instead of `TEXT` (probably not — eBay's own category/
condition sets change over time, `TEXT` avoids a schema migration every
time eBay adds one), and confirming `legacy_fields`' member list against
a real scan of the 55k items rather than the reference doc's static list.

### Index design

Grounded in what's actually queried today (catalog rebuild, `tgw search`,
eligible-filter UI, `sqlite_catalog.py`'s existing `idx_location`/
`idx_status`/`idx_title` as the direct precedent — Postgres inherits the
same three plus what SQLite's flat-catalog design couldn't do):

```sql
CREATE INDEX idx_items_status            ON items.items(status);
CREATE INDEX idx_items_location          ON items.items(location);
CREATE INDEX idx_items_category          ON items.items(category);
CREATE INDEX idx_items_ebay_offer_status ON items.items(ebay_offer_status);
CREATE INDEX idx_items_ebay_listing_status ON items.items(ebay_listing_status);
CREATE INDEX idx_items_draft_state       ON items.items(draft_listing_state);
CREATE INDEX idx_items_updated_at        ON items.items(updated_at);  -- incremental catalog sync / cache invalidation
CREATE INDEX idx_history_sku_ts          ON items.item_attributes_history(sku, ts);
CREATE INDEX idx_specifics_sku_ts        ON items.item_specifics_history(sku, ts);
CREATE INDEX idx_outbox_unapplied        ON items.mutation_outbox(created_at) WHERE applied_at IS NULL;
```

**Deliberately NOT indexed yet**: no `GIN` index on any `jsonb` column
(`item_attributes`, `draft_listing`, etc.) — nothing today queries inside
those envelopes at the database level (Set A/B accessors read the whole
envelope into Python and filter there); adding a `GIN` index speculatively
is exactly the kind of premature optimization this project's own
[[resimplify-principle]] warns against. Revisit only if P4's read-cutover
work finds a real query that needs it.

### Dual-write mechanics (P3, the pilot)

**Recommended pilot field: `status`.** Single scalar column, directly
ties to the #1377/#1376 incident this whole PP exists to prevent, and —
critically for a *reversible* pilot — rolling it back means simply
stopping the dual-write call, since JSON stays authoritative throughout
P3. No data to unwind, no schema to reverse.

**The choke point already exists — extend it, don't build a new one.**
Every real writer today (`_write_field`, `set_fields`, `_apply_patch`,
`_apply_ebay_write`) already converges on the same three-step shape:
atomic JSON write → best-effort SQLite catalog upsert → fire-and-forget
NATS `publish_mutation()`. P3 adds a fourth step to this same shape, for
the piloted field only:

1. **JSON write stays first and authoritative** — unchanged. If it fails,
   nothing downstream runs, exactly like today.
2. **Postgres dual-write is a NEW step, and unlike the SQLite upsert it
   is NOT allowed to fail silently** — the whole point of P3 is proving
   parity, so a silent failure defeats the pilot's purpose. On failure,
   persist a finding via the existing `_persist_finding` pattern (same
   one used for `sqlite_catalog_upsert_failed`) with a new code,
   `pg_dual_write_failed` — same invariant-C11 discipline already applied
   to every other best-effort side-write in this codebase, not a new
   pattern invented for Postgres.
3. **Idempotency via the outbox table above**: the Postgres field update
   and an `items.mutation_outbox` insert happen in the same transaction.
   A small reconciler (not built yet, P3 scope) marks `applied_at` once
   confirmed — this is what makes a retry safe (re-processing an
   already-applied outbox row is a no-op, detectable via `applied_at IS
   NOT NULL`) and gives Tigwa's "outbox behavior" requirement a concrete
   shape rather than staying prose.
4. **Parity check**: a scheduled or on-demand job compares JSON `status`
   vs. Postgres `status` for every item. Any mismatch becomes a register
   row — same shape as PP-OPSREALITY-001's discrepancy rows, not a
   separate reporting mechanism — and is corrected by re-reading JSON
   truth into Postgres (JSON wins during P3, always), never the reverse.
5. **Rollback = stop calling the dual-write function.** No migration to
   undo. This is the concrete reason a single reversible scalar field is
   the right P3 pilot, not a jsonb envelope or multi-field family — those
   come later (P4+) once the mechanism itself is proven on the simplest
   possible case.

### P2 shadow-import mechanics

- Reuse `resolver.find_item_jsons`/`load_item_doc` — the exact functions
  every worker and CLI path already uses to enumerate items. No second
  directory-walker gets built for this.
- One script (not built), reads every item JSON, maps fields per the
  schema above, `INSERT`s into `items.items` on a disposable/sibling
  target (a scratch schema on `state_machine`, or a throwaway DB — either
  satisfies Tigwa's "no production reader/writer depends on it").
- Verification: row count against today's live baseline (55,421 per this
  session's check), field-parity spot-check against N random SKUs,
  orphan/conflict register for anything that doesn't map cleanly (legacy-
  only fields, malformed JSON — expect some given 55k items span years of
  format evolution).
- Source-hash provenance: store a hash of each source JSON file alongside
  its imported row (or a side table) so re-running the import is directly
  comparable — this is what makes the import "repeatable" per Tigwa's P2
  requirement, not just a one-off script run once and trusted forever.

### What's still open after this P1 draft

- ~~Live backup-coverage check for `state_machine`~~ — done, A5 drill
  executed and passed (see Backup contract above).
- ~~Exact column list/types/constraints and index design~~ — drafted in
  the Technical deep-dive section above; final pinning still waits on P2
  real-data population, per that section's own caveats.
- Write-volume/mutation-rate baseline — ~~waits on P0~~ P0 is done and
  live (see above); the `ITEMDATA_MUTATIONS` stream is now a live proxy
  for this — has not yet been sampled over a representative window.

## Migration scaffold — current-state inventory (Tigwa PRIORITY, 2026-07-22)

Dave, via Tigwa: "fully plan the migration scaffold now" — since the
Postgres substrate and structured dataset already exist, the reasonable
use of capacity is finishing the reviewable design/packet set, not
starting P5. This section closes the specific gaps her 7-point deliverable
named that weren't already covered above: a real readers/writers
inventory, and P2/P3 stated as bounded packets with acceptance/no-go
criteria. Everything else she asked for (target data contract, migration
topology, verification plan, backup/recovery plan) is already covered in
the sections above this one — cross-referenced, not repeated.

### Current writers of ItemData (the actual migration surface)

| Writer | Path | Notes |
|---|---|---|
| `bundle_intake` / `multi_intake` | worker | intake fields (sku, title, location, #STATUS/#VERIFIED) |
| `ai_identify` | worker | title overwrite, category, description, condition, product_lookup |
| `ebay_draft` | worker | `draft_listing.*` (includes item_specifics Set B) |
| `ebay_upload` | worker | `ebay_photos`, `draft_listing.imageUrls` |
| `ebay_price` / `ebay_price_reducer` | worker | `ebay_offer.price*`, `price_history` |
| `ebay_stage` | worker | `ebay_offer.offer_id/status`, `epid` |
| `ebay_publish` | worker (operator-triggered) | `ebay_listing.*`, `reprice_schedule` |
| `ebay_sync` | worker (periodic) | `ebay_listing.live_price/synced_at`, `ebay_offer.category_id/quantity` |
| `items.py` CLI (`_write_field`/`set_fields`/`strip_fields`) | operator/script path | bulk edits, backfills, scrub scripts — narrow-fence path, already NATS-wired |
| `http_server.py` (`_apply_patch`/`_apply_ebay_write`) | HTTP fence | the real choke point — Flutter app, web UI, most worker patches route here; already NATS-wired (P0) |

`pm_intake` deliberately excluded — DEPRECATED per CLAUDE.md, not a live
writer to plan around.

### Current readers/derived-output consumers

| Consumer | What it reads | Migration relevance |
|---|---|---|
| `sqlite_catalog.py` | full JSON, projects to scalar columns + blob | P4's nearest analog already exists — same "scalar columns + full blob" shape Postgres would formalize |
| `catalog_rebuild` worker | full directory scan | biggest P4 win — replaces a full scan with an indexed query |
| `tgw search` / eligible-filter UI | SQLite catalog primarily, JSON for detail | direct #1377-class bug surface — schema constraints fix this by construction |
| `velocity_stats` worker | scans ItemData | P4 candidate |
| recoll indexing, `tgw-view-image.sh`, MC extfs scripts | raw JSON files directly | **must keep working** — this is why P5's JSON-export-artifact leg is required, not optional, not just for humans but for existing tooling Dave already relies on |
| Radar (PP-RADAR-001, not built) | not yet — design against the eventual DB projection per PP-OPSREALITY-001's integration matrix | future, not current |

### P2 as a bounded packet

**Acceptance criteria**: script runs against a disposable/sibling target
(no production dependency); row count matches live baseline at run time
(±expected drift from ongoing writes during the run); N-random-SKU
field-parity spot-check passes; every non-mapping item lands in an
orphan/conflict register with a reason, none silently dropped; re-running
the import against the same source set produces the same result
(repeatability, via source-hash comparison).
**No-go conditions**: any field silently dropped without appearing in the
orphan register; any row miscounted without an explained delta; import
requires touching production JSON in any way (read-only only).
**Dependency**: none blocking — can start any time capacity allows, since
it targets a disposable environment.
**Owner**: tgw-coder dispatch once written as a todo/packet (not yet
filed — this section is the design, the packet itself is a small
follow-up).

### P3 as a bounded packet

**Acceptance criteria**: `status` dual-write live on the real fence
(`_apply_patch`/`_apply_ebay_write`, same choke point as P0) behind a
config flag; zero silent dual-write failures (every failure produces a
`pg_dual_write_failed` finding); parity check run daily for a defined
soak period (recommend 2 weeks minimum given ~55k items and normal
write cadence) shows 100% parity or every mismatch traced to a specific,
understood cause; rollback tested at least once during the soak (flip the
flag off, confirm JSON-only operation resumes cleanly).
**No-go conditions**: any silent mismatch that can't be traced to cause;
any code path found writing `status` outside the two known choke points
(would mean dual-write coverage is incomplete — same class of bug #1377
already proved can happen); JSON write path showing any latency/
reliability regression from the added Postgres step.
**Dependency**: P2 must be done first (schema needs to exist and be
proven importable before anything writes to it live).
**Owner**: tgw-coder dispatch, gated on P2 completing and Dave reviewing
the P2 result — not automatic.

### P4/P5/P6 — intentionally left at the design-narrative level

Per Tigwa's own calibration (previous section): these are legitimately
further out and their exact packet shape depends on what P2/P3 actually
find. Turning them into bounded packets now would be guessing ahead of
evidence — the Technical deep-dive and Phased build plan sections above
already give enough shape (acceptance gates, guardrails, sequencing) to
resume planning them concretely once P3's soak period completes.
