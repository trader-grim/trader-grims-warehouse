# TGW System Invariants

**Status:** living document. Created 2026-06-10 from a correctness/safety review of the
inventory, listing-lifecycle, and pricing code (`src/tgw/items.py`, `src/tgw/ebay/*`,
`src/tgw/workers/ebay_*`, `src/tgw/queue/*`), the existing test suite, and
`docs/architecture/{overview,services}.md`.

Each invariant lists: **where it should be enforced**, **how it could fail**, and
**how to test it**. Status legend:

- ✅ **enforced** — code enforces it; tests exist or are added in `tests/test_invariants_*.py`
- ⚠️ **partial** — enforced on the common path but with a known bypass
- ❌ **gap** — documented/intended invariant with no enforcement (or an active violation)

The gaps found by the initial review (A5, B4, C3, C4, C5) were fixed on 2026-06-10 —
see the resolution log at the end. Their original failure analysis is kept below for
context; the xfail tests that encoded them are now ordinary green tests.

Companion test files added by this review:

| File | Covers |
|---|---|
| `tests/test_invariants_items_fence.py` | A1–A3, A5 |
| `tests/test_invariants_pricing.py` | B1–B5 |
| `tests/test_invariants_reprice_schedule.py` | C7 |
| `tests/test_invariants_stage_guards.py` | C1–C2, D6 cross-check |
| `tests/test_invariants_price_reducer.py` | C4–C6 |
| `tests/test_invariants_publish_idempotency.py` | C3, C7, D5 |
| `tests/test_invariants_queue_transitions.py` | D1–D2 |

---

## A. Item store (the fence)

### A1. Item JSON writes are atomic — a reader never sees a partial file ✅
- **Statement:** every write to `ItemData/<SKU>/<SKU>.json` goes through
  `items.atomic_write_json()` (temp file in the same directory + `os.replace`).
- **Enforced:** `src/tgw/items.py::atomic_write_json`; all workers and fence helpers
  import it rather than calling `open(...,'w')`.
- **How it could fail:** a new writer opens the JSON directly; the temp file lands on a
  different filesystem (rename stops being atomic); a crash leaves orphaned `tmp*` files
  that intake/catalog code later misreads as media.
- **How to test:** unit-test that the write produces valid JSON and leaves no extra files
  in the SKU directory; grep-style audit that no `src/tgw` module writes item JSON without
  it. (Unit test added; the audit is a review-time check.)

### A2. `sku` is immutable ✅
- **Statement:** no API mutates the `sku` field; renames happen only via the dedicated
  `ebay_sku_migrate` machinery, which records `sku_history` + a rollback manifest first.
- **Enforced:** `src/tgw/http_server.py:260` (PATCH rejects with 400); the CLI has no
  sku-edit command; `items.BULK_FIELD_KEYS` whitelist excludes `sku`.
- **How it could fail:** a new bulk-edit field or MCP tool exposing raw field writes
  without the whitelist; `update_item(cfg, sku, 'sku', ...)` is *not* blocked at the
  `items.py` layer — only at the HTTP layer.
- **How to test:** existing `tests/test_http_server.py` covers the PATCH rejection;
  `tests/test_invariants_items_fence.py` pins the bulk-edit whitelist. A fence-level
  guard in `_write_field` would let this be enforced everywhere (suggestion, not made).

### A3. Item creation never overwrites an existing item ✅
- **Statement:** intake must never clobber an existing `<SKU>.json`.
- **Enforced:** `items.create_item` raises `FileExistsError`; bundle_intake relies on it.
- **How it could fail:** SKU collision from a non-camera source (manual SKU typo); a
  writer calling `atomic_write_json` directly to "create".
- **How to test:** unit test that `create_item` raises on an existing path (added).

### A4. All ItemData paths come from `config.sku_dir/sku_json/location_dir` ⚠️
- **Statement:** nothing outside the platform layer constructs `ItemData/...` paths.
- **Enforced:** by convention + review. Note the workers themselves build
  `self.config['itemdata_root'] / sku / f'{sku}.json'` inline (e.g. `ebay_price.py:50`,
  `ebay_stage.py:53`, `ebay_publish.py:94`) instead of calling `config.sku_json` — same
  result today, but it duplicates the path formula the fence is supposed to own.
- **How it could fail:** the layout changes (e.g. PP-PORTABLE-CATALOG satellites) and the
  inline copies drift.
- **How to test:** a grep audit (`itemdata_root.*\.json` outside `config.py`/`items.py`)
  in CI would pin this; not added as a pytest because it is a style gate, not behavior.

### A5. Any operator-facing field write clears the `catalog_verified` hall-pass ✅
- **Statement:** PP-VERIFY-001 — an operator "verified clean" stamp must not survive a
  subsequent mutation of the item.
- **Enforced:** `items._write_field` pops `catalog_verified` for any field except
  `catalog_verified` itself; HTTP PATCH routes through it; `items.verifiedupdate()`
  clears it explicitly (bypass fixed 2026-06-10).
- **Remaining scope note:** pipeline workers (`ebay_price`, `ebay_stage`,
  `ebay_publish`, reducer, sync) write whole item docs via `atomic_write_json` and do
  not clear it — intentional: they touch `ebay_*` mirror blocks, not the
  operator-verified physical fields the hall-pass certifies. The invariant is therefore
  scoped to operator-facing writes.
- **How to test:** unit tests on `_write_field` + `verifiedupdate`
  (`tests/test_invariants_items_fence.py`, pass).

### A6. `location` writes keep the symlink tree in sync ✅
- **Statement:** changing `location` must update `by-location/` (remove old link, create
  new one); bulk edits of location must route through the same helper.
- **Enforced:** `items.locationupdate`; `items._bulk_write` special-cases `location`;
  `http_server` routes location PATCHes to `locationupdate`.
- **How it could fail:** a new write surface calls `update_item(..., 'location', ...)`
  directly; symlink ops are not atomic with the JSON write (crash between the two leaves
  a stale link until next full rebuild — accepted, rebuild is the reconciler).
- **How to test:** covered by existing `tests/test_items.py`/HTTP tests; the rebuild is
  the safety net, so no new test added.

### A7. Every mutation enqueues a coalesced `catalog_rebuild`; rebuild is never inline ✅
- **Statement:** derived stores (SQLite catalog, JSON/CSV, thumbnails) are read models;
  writers signal staleness via the `catalog_rebuild:pending` dedupe job and never call
  `build_all_catalogs()` inline.
- **Enforced:** every worker's post-write `enqueue_job(... dedupe_key='catalog_rebuild:pending',
  not_before=+30s ...)` wrapped in `except UniqueViolation: pass`.
- **How it could fail:** a writer forgets the enqueue (catalog goes stale until the next
  unrelated write); the `UniqueViolation` pass-through is correct but means nobody checks
  Postgres is actually reachable — a writer with a dead DB writes the JSON and silently
  skips the rebuild signal (`ebay_price.py:146-154` would raise out of `handle` → retried,
  acceptable).
- **How to test:** worker-level tests stub `enqueue_job` and assert it was called with the
  dedupe key (covered incidentally by stage/reducer tests added here).

### A8. Item media files are canonical — mutation requires archive-before-modify ⚠️
- **Statement:** photos/videos in `ItemData/<SKU>/` are part of the canonical record —
  EPS re-upload, re-identification, and the visual fingerprint index all depend on the
  originals. Any operation that renames, overwrites, or derives-in-place a production
  photo must first copy the original to `data/history/ItemData/<sku>/` (the pattern
  `tgw alt-text` established 2026-06-11: archive → rename to `<SKU>-alt.jpg`).
- **Enforced:** per-feature only — the alt-text writer implements archive-then-rename
  (covered by `tests/test_alt_text.py`); there is **no general fence-level guard** for
  media writes (A1–A7 cover JSON only).
- **How it could fail:** a future feature (photo rotation, bulk re-compression, the
  researched GDrive→EPS pipelines) mutates media directly; a crash between archive and
  rename leaves a half-renamed photo set.
- **How to test:** the alt-text tests pin the established pattern. A general guard would
  require routing media writes through a fence helper — suggestion, not made. Review rule
  meanwhile: **any diff touching files in a SKU folder other than `<SKU>.json` must show
  its archive step.**

---

## B. Pricing

### B1. The price floor applies to ALL prices regardless of source ⚠️
- **Statement:** `max(per-group floor, global_floor)` from `category-groups.json` is a
  hard lower bound on any computed price (Browse p25, group assumption, config default).
- **Enforced:** `pricing._apply_floor`, called on every return path of `suggest_price`.
- **How it could fail:**
  - `category-groups.json` missing/corrupt → `_load_groups` caches `{}` and the global
    floor silently becomes the hardcoded 0.99;
  - the module-level cache (`_groups_cache`) never refreshes in a long-lived worker —
    floor edits require a worker restart;
  - the launch price is derived from the raw comp max, but is clamped to the floored
    target — see B4.
- **How to test:** unit tests with a temp groups file covering group floor, global floor,
  and the floor-on-Browse-results path (added, pass).

### B2. `to_99` never lowers a price and always lands on a .99 point ✅
- **Statement:** the .99 rounding used for launch/assumed prices is monotonically
  non-decreasing (a "round" must never give away money) and idempotent.
- **Enforced:** `pricing.to_99`.
- **How it could fail:** float-edge regressions (e.g. `to_99(16.00)` choosing 15.99).
- **How to test:** property-style unit tests over representative values (added, pass).

### B3. A price is never written without provenance ✅
- **Statement:** whenever `ebay_offer.price` is set, `price_source`, `price_comps`, and
  `priced_at` are set in the same write.
- **Enforced:** `workers/ebay_price.py::handle` writes all four together.
- **How it could fail:** manual price edits via PATCH set `draft_listing.price` without
  provenance (accepted — `price_source` absent ⇒ manual); a new pricing path forgets.
- **How to test:** covered by the worker-level pricing test (added) asserting the trio is
  present after `handle()`.

### B4. Launch price ≥ target price (markdown only moves down) ✅
- **Statement:** `ebay_offer.price` (launch, `to_99(1.10 × comp max)`) must be ≥
  `ebay_offer.target_price` (floored p25) — otherwise the day-3/17 "markdown" raises the
  price, breaking C5 and the strikethrough story.
- **Enforced (fixed 2026-06-10):** `workers/ebay_price.py::handle` clamps
  `launch = to_99(suggested)` whenever the raw-comp launch falls below the floored
  target (e.g. comps max = $1.00 with a $5 group floor → launch $5.99, not $1.99).
- **How it could fail:** a new pricing path setting launch/target independently.
- **How to test:** worker test constructing exactly that comp shape
  (`tests/test_invariants_pricing.py::test_launch_price_at_least_target_price`, pass).

### B5. Below `MIN_COMPS` usable comps, the price stays null — never a guess ✅
- **Statement:** with < 3 comps and no group/config fallback, `price` is `None` and
  `source='insufficient_data'`; `ebay_stage` is **not** enqueued for unpriced items.
- **Enforced:** `suggest_price` stage chain; `ebay_price.py` only enqueues `ebay_stage`
  when `suggested is not None`.
- **How it could fail:** the condition filter thinning comps below 3 — handled:
  `_best_prices` falls back to the unfiltered set (tested, added).
- **How to test:** unit tests for the insufficient-data path and the condition-filter
  fallback (added, pass).

---

## C. Listing lifecycle (stage → publish → reprice → sync)

### C1. Never stage with a null price, zero photos, an Active listing, or an unresolved legacy listing ✅
- **Statement:** `ebay_stage` creates eBay-side objects only when the item is fully
  prepared and provably not already listed (duplicate-listing safety = money safety).
- **Enforced:** `workers/ebay_stage.py::handle` — four ordered guards: Active-listing
  skip, legacy `Item number` skip (ISS-008 caveat), `offer_id` idempotency skip, then
  retryable `RuntimeError`s for missing draft/price/photos.
- **How it could fail:** legacy-resolution data is not authoritative (ISS-008);
  `price = draft.get('price') or ebay_offer.get('price')` treats a literal `0`/`0.0`
  price as "no price" (falsy) — currently safe because B1 floors everything ≥ 0.99;
  the "no photos" error string must stay in sync with `_TRANSIENT_ERRORS` (see D6).
- **How to test:** guard-by-guard worker tests with `stage_draft` stubbed, asserting it
  is never called when a guard trips (added, pass), plus a cross-check that the photo
  error message still classifies as transient-requeue (added, pass).

### C2. Staging never publishes ✅
- **Statement:** the automated pipeline's terminal state is an UNPUBLISHED offer; only
  the operator gate (C3) goes live.
- **Enforced:** `ebay/sync.py::stage_draft` never calls `publish_offer`; the stage worker
  writes `ebay_offer.status='UNPUBLISHED'`.
- **How it could fail:** someone wiring `publish_draft()` (the convenience wrapper that
  *does* both) into a worker.
- **How to test:** stage-worker test asserts the item ends UNPUBLISHED with no
  `ebay_listing` block (added); review rule: `publish_draft` must never appear in
  `workers/`.

### C3. Publishing is operator-gated and is the only transition to `status=Active` ✅
- **Statement:** nothing enqueues `ebay_publish` automatically; only `tgw publish`
  (`api.py::cmd_publish`) does, and only for offers in `UNPUBLISHED` status. The worker
  itself skips items whose `ebay_listing.status` is already `Active`.
- **Enforced:** `cmd_publish` checks `offer.status == 'UNPUBLISHED'` before enqueueing
  (grep confirms no other enqueuer); `workers/ebay_publish.py::handle` has an
  already-Active skip guard (added 2026-06-10) so a directly-enqueued or replayed job
  cannot re-publish or overwrite `reprice_schedule` (the markdown clock).
- **How it could fail:** a new enqueuer wired into the pipeline; removal of either
  guard.
- **How to test:** `tests/test_invariants_publish_idempotency.py` — already-Active
  skip, replayed-job no-op, and the happy publish path (pass).

### C4. An offer PUT always carries the complete offer body (full-replace semantics) ✅
- **Statement:** eBay's `PUT /sell/inventory/v1/offer/{id}` is a full replacement —
  omitted fields are stripped from the live offer. Every PUT must send the full body
  built by `_build_offer_bodies` (this was a closed pre-publish bug; `services.md` §12–13
  says it "must not regress").
- **Enforced (fixed 2026-06-10):** the original violation —
  `workers/ebay_price_reducer.py::_reduce_item` PUT `{'pricingSummary': {...}}` only —
  now rebuilds the complete offer body via `sync._build_offer_bodies` with the new
  price injected, and refuses (error-counted skip) when `draft_listing` is missing
  rather than falling back to a partial PUT. Every applied reduction also appends a
  **`price_history`** event to the item JSON:
  `{ts, price, previous_price, stage, label, source: 'ebay_price_reducer'}`.
  Side note: the reducer now makes one `_get_merchant_location` GET per worker process
  (cached), where it previously made none.
- **How it could fail:** a new `/offer/` PUT call site hand-rolling a partial body.
- **How to test:** `tests/test_invariants_price_reducer.py::test_offer_put_body_is_complete`
  asserts the full key set; `test_applied_reduction_appends_price_history` covers the
  audit trail (pass).

### C5. The price reducer never raises a price ✅
- **Statement:** documented critical invariant (`services.md` §13). Markdown stages move
  the price down only; a schedule/manual-edit mismatch must not produce an increase.
- **Enforced (fixed 2026-06-10):** `_reduce_item` compares the due stage price to the
  current `ebay_offer.price`; a stage at or above the current price is **stamped
  `done_at` without an eBay call** (so a manually-cut price is not "restored" and the
  stage does not re-fire every 6 h), counted in `stats['skipped']`, and logged as
  `ebay_price_reducer_stage_satisfied`. No `price_history` entry is written since no
  price changed. B4's launch clamp removes the other entry point for this failure.
- **How it could fail:** removal of the clamp; a price written as a non-numeric string
  (coercion guard falls back to applying the stage — acceptable, that is the
  no-current-price case).
- **How to test:** `tests/test_invariants_price_reducer.py::{test_reducer_never_raises_price,
  test_equal_price_stage_is_satisfied_without_put}` (pass).

### C6. Reducer write-ordering and skip rules ✅
- **Statement:** (a) `reprice_skip: true` is honored; (b) only items with an `offer_id`
  and an Active/PUBLISHED listing are touched; (c) the highest due stage is applied and
  all due stages are stamped `done_at` exactly once (catch-up after downtime applies one
  PUT, not three); (d) on eBay rejection the local JSON is **not** modified (no
  `done_at`, no price write) so the stage stays due for the next pass.
- **Enforced:** `_reduce_item` (the early `return` in the HTTPError branch provides d).
- **How it could fail:** (d) inverts if anyone moves the JSON write above the PUT;
  a crash *after* the PUT but *before* the JSON write re-applies the same price next
  pass — idempotent on eBay's side, acceptable.
- **How to test:** unit tests for a–d with `ebay_put` stubbed (added, pass).

### C7. `reprice_schedule` is computed once, at publish, from comps + config ✅
- **Statement:** the schedule is frozen into the item at publish (`done_at` stamped on
  launch); later config changes affect only future publishes. Stages with no price data
  get `price: null` (reducer skips them) rather than raising.
- **Enforced:** `workers/ebay_publish.py::_build_reprice_schedule` + `handle`.
- **How it could fail:** comps absent and no category default → all-null schedule (item
  simply never marks down — surfaced only by ISSUES review); `due_at` arithmetic drift.
- **How to test:** pure-function tests on `_build_reprice_schedule` (added, pass) and
  the publish happy-path test in `tests/test_invariants_publish_idempotency.py`.

### C8. Sync writes only mirror fields; sold-marking is idempotent ✅ (not re-tested here)
- **Statement:** `ebay_sync`/`ebay_legacy_sync`/webhook write only `ebay_listing`,
  `ebay_offer`, `status`/`ebay_sale` mirror blocks — never draft content; `_mark_item_sold`
  may run repeatedly (webhook + poller + state-file reset) without duplicating effects.
- **Enforced:** `ebay/sync.py`, `workers/ebay_legacy_sync.py`; existing coverage in
  `tests/test_ebay_sync.py` and `tests/test_sold_recon.py`.
- **How it could fail:** a forged/erroneous webhook marks an item sold (mitigated by the
  listing-id index check + enforced signature verification — ISS-005 resolved 2026-06-12).
- **How to test:** existing tests in `tests/test_sold_recon.py`.

---

## D. Queue / work ledger

### D1. Job states change only along the allowed transition matrix ✅
- **Statement:** `queued→leased→running→{succeeded|retry_wait|failed→dead_letter|...}`;
  `succeeded` is terminal; worker-attributed transitions require a worker id.
- **Enforced:** Python-side `state_machine.{ALLOWED_TRANSITIONS,RULES,validate_transition}`;
  DB-side by the guarded `UPDATE ... WHERE state = X AND lease_owner = Y` writes and the
  `claim_queue_jobs` PL/pgSQL function.
- **How it could fail:** Python matrix and SQL drifting apart (the matrix is advisory —
  raw SQL in `state_machine.py` is what actually runs); a new helper writing an
  unguarded UPDATE.
- **How to test:** pure-function tests pinning the matrix, terminal states, and
  `next_failure_state` boundary (added, pass). DB-side behavior needs a Postgres
  integration harness — out of scope here, the matrix tests at least freeze intent.

### D2. Only the lease owner can complete/fail its job (compare-and-swap) ✅
- **Statement:** `mark_running/mark_succeeded/mark_failed/mark_dead_letter` all carry
  `WHERE state = ... AND lease_owner = %s`, so an expired-lease worker can't stomp a
  requeued job's state.
- **How it could fail:** silent no-op — the UPDATEs don't check rowcount, so a lost race
  is invisible (the job is fine; the loser's side effects may have doubled → D5).
- **How to test:** SQL-string inspection is brittle; the matrix tests + idempotency rule
  (D5) carry this. A rowcount-log in `mark_*` would make lost races observable
  (suggestion).

### D3. `dedupe_key` ⇒ at most one active job per key ✅ (DB-enforced)
- **Enforced:** partial unique index in `queue/schema.sql`; every enqueuer wraps
  `UniqueViolation`. Requires Postgres to test; existing `test_enqueue_sku.py` covers the
  caller-side contract.

### D4. Dead-letter never auto-retries; requeue clones to a fresh job ✅
- **Enforced:** no code path moves `dead_letter→queued` automatically;
  `requeue_dead_letter_job` cancels the old row and inserts a clone **without** a
  dedupe key. Covered by `tests/test_dead_letter.py`.

### D5. Every job handler is idempotent (safe under lease-expiry replay) ✅
- **Statement:** each pipeline worker has a skip condition: already-identified, draft
  present, photos all uploaded, `price is not None`, `offer_id` present, listing Active
  (publish gained its already-Active guard 2026-06-10 — see C3).
- **Enforced:** per-worker skip guards.
- **How to test:** skip-guard tests per worker (stage + publish covered by the
  invariant test files; price covered by existing `test_strikethrough.py` skip
  behavior).

### D6. Retryable-vs-fatal classification is explicit and fail-closed ✅
- **Statement:** `HardFailure` ⇒ immediate dead_letter + notify; unknown errors at the
  retry limit dead-letter (fail-closed); only listed substrings requeue. Workers that
  signal "wait for an upstream worker" via error text must use a phrase
  `classify_dead_letter` recognizes.
- **Enforced:** `worker_base.classify_dead_letter` + `_TRANSIENT_ERRORS`.
- **How it could fail:** rewording an error message (e.g. ebay_stage's
  `'no eBay photo URLs yet'`) breaks the coupling silently — the job dead-letters
  instead of waiting for `ebay_upload`.
- **How to test:** existing `test_catalog_verify.py` covers the classifier itself; a new
  cross-check test asserts the *actual* stage-worker error string still classifies as
  requeue (added, pass). Same pattern recommended for the token-expiry string when the
  eBay client's wording is touched.

### D7. `requeue_with_backoff` resets `attempt_count` — transient loops are unbounded by design ⚠️
- **Statement/risk:** a permanently-"transient" error (token never refreshed) loops
  forever with only warning-level notifies. Accepted (notify + `tgw health` surface it),
  but it is the mechanism behind the 2026-06-08 zero-work stall class.
- **How to test:** not unit-testable meaningfully; covered operationally by health
  checks. Listed so the trade-off stays visible.

---

## E. Cross-cutting (documented elsewhere, listed for completeness)

- **E1 Output contract** — every CLI/API call returns one `{ok, ...}` JSON object
  (`api.py` main; existing tests assert per-command).
- **E2 Secrets** — only under `secrets_root` (700/600, user `tgw`); eBay scopes locked.
  Enforced by `scripts/tgw-permissions-reset.sh --check` via `tgw health`.
- **E3 Ollama single-flight** — advisory lock 8472 (`queue/ollama_lock.py`); needs
  Postgres to test; enforced at the one call site.
- **E4 Catalog-derived data is regenerable** — no data lives only in a catalog
  (`catalog_rebuild` is a full rebuild from ItemData).

---

## Resolution log

**2026-06-10** — all gaps from the initial review fixed (operator decision: C4 resolved
as full-replace **plus price history**; the rest applied as recommended):

1. **C4** — `ebay_price_reducer._reduce_item` now rebuilds the complete offer body via
   `sync._build_offer_bodies` (full-replace safe) and appends a `price_history` event
   (`{ts, price, previous_price, stage, label, source}`) on every applied reduction.
   Missing `draft_listing` → error-counted skip, never a partial PUT.
2. **C5** — reducer never raises a price: a due stage at/above the current price is
   stamped `done_at` without an eBay call.
3. **B4** — `ebay_price` clamps launch to `to_99(target)` when raw comps would put
   launch below the floored target.
4. **C3/D5** — `ebay_publish.handle` skips items whose listing is already Active
   (replay/direct-enqueue safe); dead `_PERCENTILE_KEYS` constant removed.
5. **A5** — `verifiedupdate` now clears `catalog_verified`.

The former strict-xfail tests were converted to ordinary green tests in the same change.
Affected workers need a restart to pick up the changes:
`systemctl restart tgw-worker@ebay_price tgw-worker@ebay_price_reducer tgw-worker@ebay_publish`
(`items.py` is also picked up by tgw-http/MCP on their next restart).

---

## E5 — No data is ever deleted without archiving first ❌ (gap — 2026-06-28)

**Rule:** No item JSON, photo, or associated file may be deleted, overwritten, or
removed from ItemData until the current state has been written to the ItemArchive
(`/media/db/masterarchive/history/ItemArchive/<sku>.zip`). The archive is the only
place data may be culled, and only by explicit operator decision.

**Why:** On 2026-06-28, 49 item JSONs (May 2021 paperback books, SKUs
`tgw202105091454567`–`tgw202105091545326`) were found missing from all live data sources
— ItemData, btrfs snapshots, Google Drive, and the tgw-claude-dump. They were recovered
**solely** because the ItemArchive zips existed at
`/media/db/masterarchive/history/ItemArchive/<sku>.zip`. Without those zips the data
would have been permanently lost. The archive is the last line of defense.

**Where to enforce:**
- The fence (`POST /api/items/{sku}` delete or replace path) must zip the current
  ItemData directory into the ItemArchive before any destructive write.
- `atomic_write_json` (legacy path, pre-fence) must do the same.
- `ebay_sku_migrate` must archive the old SKU directory before renaming.
- No worker, script, or operator command may `rm -rf` an ItemData directory without
  first calling the archive step.

**How it could fail:**
- A worker calls `shutil.rmtree` or `os.remove` directly on ItemData paths.
- A migration script renames without archiving the old state.
- An operator runs `rm` manually under time pressure.

**How to test:**
- Add a grep audit to CI: no file in `src/tgw/` may call `shutil.rmtree`,
  `os.remove`, or `os.unlink` on a path containing `ItemData` without a preceding
  archive call.
- `tests/test_invariants_items_fence.py`: mock a delete request to the fence and assert
  the archive zip is created before the directory is removed.

**Status:** ❌ gap — archive step exists as a manual operator habit (ItemArchive on
`/media/db/masterarchive`) but is not enforced in any code path. Must be added to
PP-FENCE-001 Session A as a required fence behaviour before workers restart.

---

## E6 — Timestamps are stored tz-aware UTC; local time exists only at render ✅ (2026-07-02, session 42)

**Rule:** Any timestamp written to durable storage (item JSON, Postgres, state
files) must be timezone-aware UTC (`datetime.now(timezone.utc)`, ISO 8601 with
offset). Naive `datetime.now()` / deprecated `utcnow()` are forbidden in `src/`.
Human-facing rendering (web UI, labels, filenames the operator reads) converts at
display time — web UI via `http_server._local_ts()` (America/Los_Angeles);
CLI/label sites via explicit `datetime.now().astimezone()`. Schedule logic tied to
an external clock (e.g. eBay's 00:00 PST quota reset) uses an explicit
`ZoneInfo("America/Los_Angeles")`, never the host timezone.

**Why:** Session 41: Postgres session tz (GMT) + 13 display sites truncating the
`+00:00` offset made a dead-letter job appear timestamped "7 hours in the future."
Verified 2026-07-02: no stored data was ever wrong — `queue_jobs` columns are all
`timestamptz` (UTC internally) and journald stores UTC — the ambiguity was
rendering-only, plus 6 naive `datetime.now()` sites (reports/health/printing/
http_server ×2), all fixed session 42. To correlate logs across surfaces, view in
one zone: `journalctl --utc` beside raw ISO values. Note psql already prints the
`+00` offset on timestamptz — bare-looking values in the web UI were the only trap.

**Enforcement:** grep gate candidate: `grep -rn 'datetime.now()\|utcnow()' src/tgw/`
must return only `.astimezone()`-wrapped render sites (currently 5). No CI hook yet.

---

## E7 — Every eBay response is captured raw at the fence ✅ (2026-07-02, session 42)

**Rule:** Every response eBay sends us — REST, Trading XML, EPS, success or
error — is appended to `incoming/ebay/YYYY-MM-DD.jsonl.gz` by
`capture_response()` inside the client choke point (`apis/ebay/client.py`)
BEFORE any worker parses it. Preservation is not a worker responsibility; a
worker cannot forget it because it never owned it. Bodies over 5 MB are
recorded as metadata (large downloads keep their own raw asset, e.g. the bulk
taxonomy gz). Capture is fail-open: a capture failure never breaks the call it
preserves. Config: `ebay_capture_enabled` (default true), `ebay_capture_root`.

**Why (PRIME DIRECTIVE 1):** Dave's day-1 requirement — get, use, and preserve
the full eBay data set — was violated for three weeks because it lived as plan
prose rather than as code at a choke point: workers pulled from the API,
extracted the fields they wanted, and discarded the rest. This invariant makes
that class of loss structurally impossible for everything eBay sends from
2026-07-02 forward. Enforced by `tests/test_ebay_capture.py`.

---

## C9 — Uninspected AI content never reaches a live listing ✅ (2026-07-02, session 42)

**Rule:** No AI-regenerated listing content (draft text, aspects, price, photos)
is pushed to a LIVE eBay listing automatically. A force update of a live listing
executes only when the job payload carries `origin='operator'` — set exclusively
by UI/CLI actions where a human inspected the content and pushed the button
(item editor "Update Listing" / revision apply). `ebay_draft` completing on a
live item logs `ebay_draft_live_update_pending` and stops; `ebay_stage` refuses
operator-less force jobs against live listings (`ebay_stage_blocked_uninspected`).
Pre-publish force re-stages of UNPUBLISHED offers are unaffected.

**Why:** Dave, 2026-07-02: "we cannot have uninspected AI changes going live
automatically yet. They are rarely correct so far." Enforced at the worker, not
just at the enqueuer, so no future enqueue path can bypass it.

**Enforcement:** guard in `workers/ebay_stage.py` handle(); tests in
`tests/test_invariants_stage_guards.py`.

## C10 — An operator action stays an operator action end-to-end 🔶 (2026-07-03, session 43)

**Rule:** Every job enqueued from an operator surface (item-action endpoint,
bulk actions, PATCH auto-redraft, dead-letter retry button, revision apply)
carries `origin='operator'` in its payload. Every pipeline worker that enqueues
a downstream pipeline job propagates that origin (draft→price/upload,
price→stage, stage→publish, publish→force-restage, upload→rate-limit requeue).
`worker_base._process` runs `origin='operator'` jobs in the **interactive**
quota context — counted, never background-halted — and restores background
context afterwards. Background/scheduled work never carries the origin.

**Why:** Dave, 2026-07-03: "this should be the behavior of List on eBay, it is
an operator action… encode that into all of the operator action surfaces."
Three consecutive days of EPS quota exhaustion by background debris blocked all
operator listing work while `quota.py`'s 30% operator reserve sat unreachable —
no code path ever ran operator-triggered uploads as interactive. Same field as
C9's inspection gate: `origin='operator'` means a human pressed the button, and
both the content gate and the quota lane key off it.

**Enforcement:** context switch in `queue/worker_base.py` `_process()`;
origin stamps at all 14 operator enqueue sites in `http_server.py`;
propagation in `workers/ebay_draft.py`, `ebay_price.py`, `ebay_stage.py`,
`ebay_publish.py`, `ebay_upload.py`. Tests: `tests/test_operator_origin.py`.
🔶 = detector pending: no CI check yet that a NEW operator endpoint stamps the
origin — candidate: grep-audit like the fence's, or a shared enqueue helper.
