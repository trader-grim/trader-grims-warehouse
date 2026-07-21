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
  scoped to operator-facing writes. `ebay_sku_migrate.py`'s three post-rename
  `atomic_write_json(new_path, item, ...)` sites (audit#1143 #1171, finding 10) belong
  to the same accepted class: they load the whole item doc, mutate `ebay_listing`/
  `ebay_offer` fields in memory, and write it back after `os.rename()` — same
  read-modify-write-whole-doc shape as the pipeline workers above, same lost-update
  race against a concurrent operator PATCH. Previously undocumented ("not on the
  tracked-gap list" per the audit); now tracked here rather than fixed, since a real
  fix (optimistic-concurrency check or a narrower field-patch primitive that survives
  a just-completed SKU rename) is a bigger structural change than this batched
  cohesion pass scopes for.
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

### C6.5. An operator-set price is never overwritten by the auto-price chain ✅ (PP-PHOTOSYNC-001 P5, todo #1120, 2026-07-03/04)
- **Statement:** `workers/ebay_price.py::handle` refuses to compute/write a fresh
  price when `price_history[-1].source == 'operator'` and the job does not carry
  `origin: 'operator'` — a chain-enqueued (draft→price) job has no consent to
  override a manually-typed price. The Re-price button's own `origin: 'operator'`
  stamp is the consent signal (same field as C10), so it still overrides its own
  prior operator entry.
- **Enforced:** early-return guard in `ebay_price.py::handle`, before any comps
  query; persists a durable finding (`ebay_offer.price_guard_skipped`, invariant
  C11) rather than a log-only skip.
- **How it could fail:** a new price-writing path that doesn't check `origin`, or
  a caller that stamps `origin: 'operator'` without genuine operator action
  (would need auditing at the enqueue site, same as any C10 site).
- **How to test:** `tests/test_invariants_pricing.py` (4 new cases: chain-skip
  persists finding, operator-origin override works, non-operator history source
  doesn't trigger the guard, already-priced idempotent skip unaffected).

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
  Single-value provider keys (LLM + lookup APIs) go through ONE facility as
  of 2026-07-09 (todo #1252): `secrets_root/tgw.env` (`KEY=value`, sourced
  into the process environment by `tgw.config.load_config()`), read via
  `tgw.apis.secrets.get_api_key(provider)`/`get_secret(name)` — never a
  per-provider `<name>-credentials.json` reader anymore. Structured
  multi-field credentials (eBay app/token, tgw-api-key) stay as JSON files.
  See `TGW-Config-Reference.md`.
- **E3 Ollama single-flight** — advisory lock 8472 (`queue/ollama_lock.py`); needs
  Postgres to test; enforced at the one call site.
- **E4 Catalog-derived data is regenerable** — no data lives only in a catalog
  (`catalog_rebuild` is a full rebuild from ItemData).

---

## E15 — Model IDs are never hardcoded, anywhere — config is the only source, including fallback chains and shared defaults ⚠️ (2026-07-20, Dave — OPEN, no detector yet)

**Statement:** no provider/model string may appear as a literal in `src/`
outside `tgw-models.json` itself — not as a working default, not as a dead
fallback nobody reads, not in CLI help text, not in a code comment claiming
"the current model is X." `get_task_model()` (E8/2026-07-09's rule) already
established config as the only source for a task's *primary* provider/model;
this extends the same rule to (a) code that never actually executes but
still names a model, and (b) the *shape* of fallback/default resolution
itself.

**Why (Dave, 2026-07-20):** triggered by finding two dead, stale
hardcoded strings (`config.py`'s unused `alt_text_model`/`alt_text_provider`
keys defaulting to `"google/gemini-2.5-flash"`; `api.py`'s `tgw alt-text`
CLI help text repeating the same string) — both inert, but exactly the kind
of drift the 2026-07-09 rule was written to prevent, and both named a model
generation (`gemini-2.5-flash`, no `-lite`) that is being deprecated
upstream. Dave: "models cannot be hardcoded... primary and even multiple
fallback paths, but they need to be configurable. This is clearly not a
static part of the design. Everything could change tomorrow."

**Sharper motivation, same session:** not primarily about cost or outage
resilience (see the fallback-chain non-urgency note below) — "not
unknowingly draining our reserve too. I monitor it for usage for this
reason more than for the cost. Nip it in the bud so it doesn't stick you
later when we are going full throttle." OpenRouter is the current fail-soft
reserve behind all three direct providers (E8, 2026-07-08 supersession). A
stale hardcoded `provider: openrouter` default — even inert today — is
exactly the kind of thing that silently starts executing once usage
patterns shift (e.g. a code path that currently never hits its fallback
branch starts hitting it under the higher-throughput load this whole
eBay-API-unblock effort is aiming for), draining the reserve without
tripping any obvious alarm since Dave's monitoring is watching for exactly
this pattern already. Fix stale/dead model references while they're cheap
to fix, before real throughput exercises code paths that were never
verified live.

**Design (Dave, same session): a `defaults` block in `tgw-models.json`.**
Most tasks don't need a bespoke provider/model — they should point at a
named default profile so changing "the" model for most tasks is a one-place
config edit, not N task-entry edits:

```json
{
  "defaults": {
    "default":                      {"provider": "google_direct",   "model": "gemini-2.5-flash-lite"},
    "default_deepseek_nonthinking": {"provider": "deepseek_direct",  "model": "deepseek-v4-flash"}
  },
  "ai_identify":   {"use_default": "default"},
  "alt_text":      {"use_default": "default"},
  "bulk_classify": {"use_default": "default"},
  "ebay_draft":    {"provider": "google_direct", "model": "gemini-3.1-pro-preview"},
  "pm_intake":     {"use_default": "default_deepseek_nonthinking"}
}
```

A task entry is either a full explicit `{"provider", "model"}` override
(e.g. `ebay_draft`, which deliberately runs a bigger model) or a
`{"use_default": "<name>"}` pointer — not both, no partial-merge — keeping
`get_task_model()` a two-branch lookup instead of a config-merging engine.

**Fallback chains stay config-shaped too:** `call_model()`'s existing
direct→OpenRouter fail-soft pattern (E8) already derives the OpenRouter
fallback model from the *same* `model` string via prefix transformation
(`f'google/{model}'` etc.) rather than a second hardcoded string — so a
config-only model change already propagates through the fallback
automatically. What's still hardcoded is *which provider is whose
fallback* (fixed elif-branches in `call_model()`). Not being changed in
this pass — flagged as a possible future gap, **not urgent** (Dave,
2026-07-20, same session: "technically we are small enough to handle some
outages. We have worse issues with cellular interference than real api
timeouts. We will just monitor and retry.") — provider-outage resilience
isn't TGW's actual failure mode at this scale; don't chase configurable
fallback *chains* as unprompted future work. The E15 fix in this pass
(no hardcoded model IDs, `defaults`/`use_default`) stands on its own merit
independent of this — it's about config-vs-code hygiene and deprecation
risk, not outage resilience.

**Detector:** none yet — recommend a `tests/test_no_hardcoded_models.py`
that greps `src/tgw/**/*.py` for known current/deprecated model-id
substrings (`gemini-2.5-flash"`, i.e. without `-lite`, `gemini-1.5-`, etc.)
outside `tgw-models.json`-reading code paths, failing the build on a match.

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

## E5 — No data is ever deleted without archiving first ✅ (partial — todo #1104, 2026-07-03/04)

**Rule:** No item JSON, photo, or associated file may be deleted, overwritten, or
removed from ItemData until the current state has been written to the ItemArchive
(`archive_root/<sku>.zip`, config key `archive_root`, default
`/opt/TGW/data/ItemArchive`). The archive is the only place data may be culled, and
only by explicit operator decision.

**Why:** On 2026-06-28, 49 item JSONs (May 2021 paperback books, SKUs
`tgw202105091454567`–`tgw202105091545326`) were found missing from all live data sources
— ItemData, btrfs snapshots, Google Drive, and the tgw-claude-dump. They were recovered
**solely** because the ItemArchive zips existed. Without those zips the data would
have been permanently lost. The archive is the last line of defense.

**Enforced (2026-07-03/04, todo #1104):** `items.atomic_write_json(..., archive_root=...)`
zips the on-disk JSON into `archive_root/<sku>.zip` (one timestamped entry per
overwrite) before the temp-file rename, whenever the target already exists and a
caller opts in. Archiving is fail-closed — an archive error raises and aborts the
write; it is never a best-effort try/except. Wired into `items.py`'s `_write_field`
(covers `update_item`/`update_items`/`locationupdate`/`catlocmvall`) and
`verifiedupdate`, and into `http_server.py`'s three overwrite call sites
(`_apply_patch`, `_apply_ebay_write`, the photo-order-removal PATCH). `create_item`
needs no archiving — it already refuses to overwrite (A3). Live-verified 2026-07-04:
real item `tgw201412211145262` written via `verifiedupdate()`; pre-write JSON (title,
verified, sku all intact) landed in `/opt/TGW/data/ItemArchive/tgw201412211145262.zip`
before the overwrite. Tests: `tests/test_invariants_items_fence.py` (+6, including a
fail-closed case: a monkeypatched archive failure raises and leaves the original file
untouched).

**Archive location note (2026-07-03/04):** `archive_root`'s configured symlink
(`/opt/TGW/data/ItemArchive` → `/media/TGW/store/ItemArchive`) was stale/unmounted on
tgw-prod; Dave confirmed the real, current archive (54,688 zips) temporarily lives at
`/home/db/devices/porche/history/ItemArchive` while he manually zipmerges several
archive copies together. Per Dave's direction, `/opt/TGW/data/ItemArchive` is now a
plain local directory (tgw:tgw, 750) on the NVMe root — writes accumulate there
directly rather than through any symlink, movable to another partition later without
code changes (`archive_root` is config-driven).

**Not yet done (deferred, out of scope for #1104):**
- `POST /api/items/{sku}` delete path (if one exists) — not audited this pass.
- `ebay_sku_migrate` archiving the old SKU directory before renaming (separate PP).
- No worker, script, or operator command may `rm -rf` an ItemData directory without
  first calling the archive step — not audited this pass (no known offenders found,
  but not exhaustively grep-audited like PP-FENCE-001's atomic_write_json ban).
- Photo/media-file deletion (only item JSON is covered; photos aren't zipped here).

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

## C10 — An operator action stays an operator action end-to-end ✅ (2026-07-03, session 43; detector 2026-07-03/04)

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
origin stamps at all operator enqueue sites in `http_server.py`;
propagation in `workers/ebay_draft.py`, `ebay_price.py`, `ebay_stage.py`,
`ebay_publish.py`, `ebay_upload.py`. Tests: `tests/test_operator_origin.py`.
Detector (P3, todo #1118, closes the 🔶): `tests/test_operator_origin_sourcescan.py`
source-scans every `state_machine.enqueue_job(` call in `http_server.py`
(fence-grep-audit pattern) — each site must stamp `origin="operator"` in its
payload (dict literal or an out-of-line `payload["origin"] = "operator"`
before the call) or target the allowlisted `catalog_rebuild` queue (coalesced
rebuilds never carry operator origin by design). A new unstamped, non-allowlisted
site fails the test with the offending line number.

## C11 — A skip/guard is a finding, not a log line ✅ (2026-07-03, session 43)

**Rule:** When a worker refuses to act on a real, recurring condition (not a
transient retry situation), the reason must be persisted durably on the item
— queryable by `catalog-verify`, not just written to journald where it rots.
Before acting on any escape-hatch/override for that condition, re-verify it
live against the authoritative source (the external system, not a local
static field) — a static local flag that was true once can go stale.

**Why:** Dave, s43, live during PP-PHOTOSYNC-001 P4/P10: "we have been
ignoring and not recording the error message... I instructed that we both
check for this type of issue, and for us to regularly check for and repair
any instances of it." `ebay_stage`'s legacy-listing guard had been silently
skipping and logging to journald only since at least 2026-06-20 (confirmed:
`migrate-blocked.json` already had 26/57 identical rejections recorded, never
aggregated into anything actionable). Worse, the guard's own premise (a
static local `Item number` field means "this is still a separate Trading-
managed listing") was proven wrong for 100% of a 491-item sample it was
blocking — a month of occasionally managing listings via Seller Hub directly
(a real operational gap, not a bug) can silently change what's true on eBay's
side without our local record ever updating. "It could happen again and
needs an auto repair path... check for both specifically, then resolve."

**Enforcement:** `ebay_stage.py`'s legacy-listing guard persists
`legacy_listing_blocked` on every hit (operator-triggered or background) and
runs `tgw.ebay.pull.check_legacy_duplicate_listing()` — a live Inventory-API
offer lookup compared against the locally-recorded listing_id — before ever
resolving. `cmd_resolve_legacy` runs the same live check by default. New
catalog-verify rule `legacy_listing_unrepaired` is the "regularly check"
detector. Tests: `tests/test_invariants_stage_guards.py`,
`tests/test_resolve_legacy_duplicate_check.py`.

**Second instance (todo #1304, 2026-07-13):** `multi_intake.py`'s
derived-child-SKU-collision guard had the same gap — collision with an
existing ItemData record was only `log_event`/`notify`'d, never persisted.
Fixed the same way: persists `sku_collision_blocked` (colliding_sku,
base_sku, detected_at) on the colliding item via `fence_patch_item` on every
hit, additive alongside the existing transient log/notify. New catalog-verify
rule `sku_collision_unrepaired` mirrors `legacy_listing_unrepaired`. Test:
`tests/test_multi_intake.py`.

## C12 — Item field-sets are self-describing envelopes, read/written only through their named accessor ✅ (2026-07-15, todo #1418, PP-LISTEDITOR-001)

**Rule:** `item_attributes` (Set A — "Inventory Record": universal,
marketplace-agnostic facts) and `draft_listing.item_specifics` (Set B —
"eBay Draft": the eBay-specific, category-mapped values actually pushed to
eBay's Inventory API) — and any future marketplace-specific set — are
self-describing envelopes (`{_set, version, updated_at, fields}` + an
append-only provenance history array), never bare dicts. They are read and
written ONLY through their named accessor module: `tgw.inventory_record`
for Set A, `tgw.ebay.draft_specifics` for Set B. No other file may index
directly into either dict's contents (`item.get("item_attributes")[...]`,
`draft_listing["item_specifics"][...]`, etc.) — and no code may move data
between the two sets via a per-key merge, prefill fallback, or `{**a, **b}`
spread performed locally; that belongs in one explicit, named translation
function (see #1416/#1417) built on top of the accessors, never ad hoc.

**Why:** Dave, this session: "The problem is you are considering keys
individually... They are sets of data. If you don't look at it that way
you will keep mixing them up." Confirmed live: two prior sessions (#1291,
2026-07-13; #1313/#1316, 2026-07-13) each fixed a real, well-tested bug in
this exact territory without ever noticing the set-boundary problem
underneath, because the old bare-dict shape had no self-identifying
marker — indistinguishable at a glance from any other dict in the item
JSON. `item_attributes` was also entirely undocumented in
`TGW-Item-JSON-Schema.md` before this packet (confirmed: zero grep hits).
A full codebase audit (todo #1416's investigation) found FOUR independent
code paths that had each separately reinvented a (wrong) use for
`item_attributes`, including one whose own UI banner text contradicted
what the code actually did.

**Enforcement:** `tgw.inventory_record` (Set A) and `tgw.ebay.draft_specifics`
(Set B) are the sole sanctioned accessor modules — see their banner
comments for the full two-set explanation, cross-referencing this
invariant and todo #1418/#1416/#1417. Detector: a static, commit-time
grep-based check (`tests/test_invariant_c12_field_set_accessors.py`),
chosen over a catalog-verify (data-scan) detector because a static check
catches a violation the moment the code is written, before it ever
touches live data — a data-scan detector can only ever notice AFTER a
corrupting write has already happened. The test pins an explicit,
reviewed allowlist of every legitimate non-accessor hit (accessor output
being written onward, or an unrelated dict — e.g. an AI model response or
a `revision_draft.delta` proposal — that happens to share a key name); any
new, unreviewed hit fails the test. `#1416`'s drift detector (catalog-verify
rule `field_set_drift`, `tgw.api._verify_item`) is the complementary
"regularly check and repair" half, for DATA drift rather than CODE drift —
landed 2026-07-15, flags any item with a live `ebay_offer.offer_id` where
Set A and Set B disagree on an overlapping key (severity: warning; dry-run
by default, matching every other catalog-verify rule — no auto-repair).
Spot-checked live against `tgw202605040949058` (the item cited in #1416's
own investigation), which shows the real, confirmed drift
(`Type: "Lapel Pin"` vs `"Brooch"`) at the time #1416 landed.

**Migration note:** the envelope shape landed via
`scripts/migrate_field_set_envelope.py` (dry-run + accessor back-compat
for both shapes — pre-migration bare dicts are read transparently by the
accessors). The full 55k-item catalog migration is a separate, explicit
go/no-go decision, not bundled into this invariant landing — both the old
bare-dict shape and the new envelope shape must (and do) coexist correctly
for as long as that transition takes.

## C13 — Set A writes are gated, provenance-recorded, operator-reviewed proposals — never a silent auto-promotion ✅ (2026-07-15, todo #1417, PP-LISTEDITOR-001)

**Rule:** `item_attributes` (Set A) may be written by an eBay-draft-discovered
value ONLY through an explicit, named, provenance-recording write path —
`tgw.ebay.inventory_diff.apply_inventory_diff()` — never a generic PATCH
passthrough, and never silently/automatically (no confidence-threshold
auto-promotion). Every promotion is an explicit operator submit against a
default-checked diff (Dave, 2026-07-15: "presenting a selectable diff
offering to update the inventory record, all differences checked by
default, operator can uncheck or skip altogether. Gated automatic
update."). This is C12's write-discipline extended specifically to the
REVERSE (Set B -> Set A) direction; C12 itself already covers "read/write
only through the named accessor" for both sets and both directions.

**Why:** #1416's investigation (see C12 above) found no reverse-flow
mechanism existed at all — an eBay-draft-discovered correction (e.g. an
AI-vision-resolved "Brooch" vs the stale universal "Lapel Pin") had no
path back into the universal inventory record, confirmed by absence, not
a bug to locate. Building that path without a review gate would have
violated the operator-gate-is-the-design precedent (C9/C10) the same way
a silent forward auto-write would have.

**Enforcement:**
- *Code level*: `tgw.ebay.inventory_diff.apply_inventory_diff()` is the
  sole sanctioned reverse-flow write function, built on
  `tgw.inventory_record.set_inventory_fields()` (never a raw dict merge).
  It re-diffs live against the item at call time rather than trusting
  caller-supplied values — a stale/no-longer-true diff key requested by a
  client is silently skipped (idempotent no-op), never force-written.
  Covered by the same C12 static allowlist detector
  (`tests/test_invariant_c12_field_set_accessors.py`) since it's built on
  the same accessor.
- *UI level*: `GET /api/items/{sku}/inventory-diff` (read-only, safe to
  call any time) + `POST /api/items/{sku}/inventory-diff/apply` (writes
  ONLY the checked subset) are a separate, distinctly-labeled panel
  ("eBay → Inventory Record sync") from the FORWARD `accept_proposals`
  banner ("Pipeline proposed changes") — no shared action name, no shared
  write path, so an operator can never confuse "accept this eBay-side
  proposal" (Set A -> Set B) with "accept this inventory-record-bound
  diff" (Set B -> Set A).
- *Data level*: catalog-verify rule `inventory_diff_unresolved_stale`
  (`tgw.api._verify_item`) flags any item where
  `tgw.ebay.inventory_diff.diff_ebay_draft_to_inventory()` finds a key
  that has disagreed for 30+ days with a known `detected_at` (never
  fabricates an age for a legacy item with no timestamp — Prime Directive
  1). Unlike `field_set_drift` (C12's data detector), this is NOT gated on
  a live `ebay_offer.offer_id` — an unresolved reverse-flow diff matters
  for a pre-publish draft too, since Dave's design intent here is routine
  review cadence, not just live-listing correctness. The 30-day threshold
  is the packet spec's proposed default, flagged as worth confirming with
  Dave rather than silently settled.

**Idempotency / re-diffing (spec point 5, confirmed design choice — see
todo #1417's result manifest for the full reasoning):** an unapplied/
unchecked diff has NO stored "dismissed" state — it simply reappears on
the next `GET /inventory-diff` call because Set A and Set B still
genuinely disagree, which is correct (the disagreement is still true).
Chosen over a sticky-skip because (a) the packet spec's own default
reasoning is "no stored dismissed state needed... an unapplied diff just
reappears... which is correct," and (b) a sticky-skip would need its own
new persisted state (a fourth thing to track per key, when C12/C13 are
already working to keep the two field-sets' bookkeeping minimal) with no
stated requirement driving it. If Dave wants a sticky skip later, that's
a small, additive change on top of this (a `dismissed_diff_keys` list
checked by the diff engine) — not a redesign.

## C14 — An operator's correction either takes effect or is visibly, actionably reported as failed — never silently lost ⚠️ (2026-07-16, incident, PP-LISTEDITOR-001)

**Rule:** when an operator submits a change to any stored field (clearing,
editing, correcting), the system must guarantee one of exactly two
outcomes — the change is durably applied and reflected back to the
operator, or its failure is surfaced as an actionable, specific finding.
A third outcome — the submission is silently accepted (200 OK) but the
value never actually changes anywhere the operator can see — is
forbidden. This is Prime Directive 1's "never discard... data" read
together with the operator-gate principle (C9/C10): the gate only works
if pressing it does something truthful.

**Why — live incident, 2026-07-16:** an operator (Dave) repeatedly
cleared `item_attributes`/`draft_listing.item_specifics.Material` on
`tgw202605040949058` (live, listed as "Sterling Silver and Gold") through
the item-detail aspects form. Each attempt reported "✓ Saved." The value
never changed, with no error, no indication, nothing to click that would
have told the operator the correction hadn't landed. The wrong material
claim stayed live and uncorrectable through the UI until root-caused same
day — the listing had to be manually ended on eBay as the only available
remedy. Root causes found, in order of discovery, each one a distinct way
this invariant was silently violated:
1. `saveEbayDraft()`'s aspects-collection loop only included a field in
   the save payload `if(v)` (non-empty) — a cleared field's key was
   dropped entirely, so the backend never even saw an attempted change
   (fixed, todo #1461).
2. Once (1) was fixed and an empty value did reach eBay, eBay's Inventory
   API rejected it outright with a garbled generic error dumping the
   entire aspects dict — `_build_offer_bodies()` had no rule for
   translating "operator cleared this" into eBay's own "omit the aspect
   key" convention (fixed, todo #1462).
3. The item-detail page's pipeline-error box could dismiss ("Clear
   error") a failed push but had no operator-initiated retry — and
   neither did the "Needs attention"/"Retry" action-line buttons, which
   only scroll to the error, they don't re-run anything (open, todo
   #1461/#1462 follow-up, not yet built).
4. `ebay_stage` (the worker behind the far more common "Update Listing"
   button on an already-live item) never refreshed the local `ebay_live`
   mirror after a successful push — only `ebay_publish` did, and only
   after being fixed same day (todo #1445). This is the mechanism behind
   "why do we keep having to manually re-sync" (Dave, 2026-07-16): the
   worker that runs on nearly every real edit never told the local record
   what actually landed (fix in progress, `tgw.ebay.sync.
   enqueue_post_push_sync()`, shared by both workers).
5. Separately, Tigwa's same-day field-set-boundary audit found the
   generic `PATCH /api/items/{sku}` endpoint still accepts a full Set A/
   Set B envelope from an authenticated caller — a live violation of C12's
   "no generic PATCH passthrough" rule, a different but adjacent way wrong
   data could enter either set outside its accessor (open, todo #1464).

**Status: ⚠️ open, not a built invariant with a detector yet.** Items 1
and 2 above are fixed and tested (offline suite green). Items 3, 4, 5 are
filed but not yet built. This invariant is being written down NOW,
concurrent with the incident, per Prime Directive 5 — not retroactively
after full enforcement exists, so it isn't lost the way the underlying
gap was for however long it predated today.

**What "detector" should mean once this is fully built** — not yet
implemented, captured here as the target: every operator-facing save path
needs a round-trip test proving a *cleared* value (not just a changed
one) actually persists and is reflected in the next read/render, the same
shape as the two regression tests `test_draft_specifics.py` and
`tests/test_http_server.py` gained today for the aspects form
specifically. A fleet-wide, path-by-path version of that check — rather
than one path fixed reactively per incident — is the real fix for "this
keeps happening in a new field/form every time," not yet scoped as its
own todo.

**Detector built, 2026-07-18 (todo #1468, PP-LISTEDITOR-001).** A
fleet-wide, path-by-path round-trip suite now exists — `tests/
test_http_server.py`'s `test_c14_*` section and `tests/test_revision.py`'s
`TestLiveApply::test_c14_*`. Every operator-facing save path in
`http_server.py` was inventoried; each path either got a set→save→clear→
save→re-read round-trip test, or is explicitly excluded with a stated
reason (see the section header comment in `test_http_server.py` and this
todo's result manifest, `plan/packets/results/1468-RESULT.md`, for the
full inventory). Currently GREEN (cleared value verified to persist):
item-detail direct field edit (`PATCH /api/items/{sku}`, bare top-level
field), the aspects form (`draft_listing.item_specifics` via the same
endpoint), bulk edit (`POST /api/bulk/apply`), and `accept_proposals`
(`POST /api/items/{sku}/action`). Excluded with reason (not a "clear an
operator-supplied value" path): `inventory-diff/apply` and
`category-aspect-migration/apply` (move data between Set A/Set B, never
discard it), `set-template` (additive only), `photo-order`/`inventory-
lock`/`remove-comp` (not field-value corrections).

**Two NEW live instances of the C14 bug class found while building this
detector, not yet fixed (both filed same day):**
1. **Todo #1522** — the base-data "padlock" auto-sync
   (`_apply_patch`'s `draft_listing` branch, the 2026-07-18 padlock
   design) silently reverts an UNLOCKED top-level `title`/`description`
   field's clear the next time *any* unrelated `draft_listing` field is
   saved (e.g. a price edit) — it resyncs the base field from the stale
   `draft_listing.description`/`.title` value with no error. Locking the
   field first is the only workaround and is not the default state.
   Regression test (currently `xfail`, will flip green once fixed):
   `test_c14_unlocked_description_clear_reverted_by_unrelated_draft_save`
   in `tests/test_http_server.py`. A control test alongside it,
   `test_c14_locked_top_level_field_clear_survives_unrelated_draft_save`,
   confirms locking IS an effective (if non-default) mitigation today.
2. **Todo #1523** — `tgw/revision.py`'s `_place_delta_in_bodies` (the
   live-push body-builder behind `POST /api/items/{sku}/revision/apply`)
   never received the #1462 fix that made `sync.py`'s
   `_build_offer_bodies` omit a cleared aspect key entirely rather than
   sending an explicit blank value — eBay's Inventory API rejects an
   empty aspect value outright per this invariant's own incident
   narrative above. A revision-apply delta clearing an aspect sends
   `{"Brand": [""]}` straight to eBay instead of omitting `Brand`, the
   exact push-boundary bug #1462 fixed for the *other* push path.
   Regression test (currently `xfail`):
   `TestLiveApply::test_c14_aspects_delta_clear_omits_key_not_blank_value`
   in `tests/test_revision.py`.

## E8 — The Google free tier is the operator emergency reserve ✅ (2026-07-04, session 45) — SUPERSEDED for background use 2026-07-08

**Original rule (free-tier era):** Background jobs never spend the Google
Gemini free tier. OpenRouter is the primary provider for all cloud LLM
tasks; `google_direct` may only be called (a) as the interactive-caller-only
fallback when OpenRouter fails — the C10 operator lane qualifies — or (b) as
a configured primary once a PAID Google key exists. Never assume a
published free-tier number applies to this project: Google doles quota per
project (~20 req/day/model observed here vs 1,000 published). Full findings
+ re-verification recipe: `reference/LLM-Providers-Quotas.md`.

**Why (original):** Dave, s45: "it's only 20 calls... make that the
operator emergency reserve. It's not very valuable otherwise." Background
use of the free tier produced 2,171 doomed 429s in one day (2026-07-04),
each burning ~40s of retry latency per requeue-backlog job, and the true
per-project grant had been rediscovered from scratch at least three times
(s41, s44, s45) because it was never written down.

**2026-07-08 update (session 48/49):** condition (b) is now live, and
extended beyond Google — Dave installed paid keys for Google, DeepSeek, and
Anthropic and asked for all three to go direct-primary with OpenRouter as
fallback only. `ai_identify`/`alt_text`/`ebay_draft`/`bulk_classify` →
`google_direct`; `pm_intake`/`suggestions_classify` → new `deepseek_direct`
(`llm.py: _call_deepseek_direct`, OpenAI-compatible chat completions
against `api.deepseek.com`); `pm_chat` → new `anthropic_direct`
(`_call_anthropic_direct`, Anthropic Messages API against
`api.anthropic.com`, system prompt extracted from a leading system-role
message since Anthropic's API takes it as a separate field). Each direct
provider fails soft to OpenRouter on any error, mirroring the existing
`google_direct` pattern exactly (same shape: precheck → try direct → catch
→ log → openrouter fallback). Live-smoke-tested both new providers
individually before rollout (real API round-trip, not just code review).

All three quota pools (`llm_google`=300, `llm_deepseek`=500,
`llm_anthropic`=100 — see `quota.py: _DEFAULT_BUDGETS`) are **provisional
safety caps, not measured limits** — Dave deliberately keeps billing credit
low until the 2026-07-01/07-04 resubmission-storm class of bug (E9, todo
#1250) is confirmed resolved. A 429 on any of these pools now means either
normal throughput hitting the cap (raise it) or a repeat of the storm
(investigate first, don't just raise the cap reflexively). The C10
operator-lane fallback behavior (openrouter → google_direct on interactive
failure) is unaffected — redundant with google_direct being primary now,
but harmless left in place. `http_server.py`'s `pm_chat` endpoint had a
hardcoded `provider != "openrouter"` guard that would have 503'd the moment
this flipped — updated to accept `anthropic_direct` too.

## E9 — One-off scripts announce themselves before doing anything ⚠️ (2026-07-08, session 48/49)

**Rule:** Any ad hoc script (bulk requeue, backfill, remediation, migration —
run by hand under `scripts/`, not a systemd worker) must call
`tgw_logging.announce_script_run(script_name, purpose, **fields)` once at
the top of `main()`, before touching the queue or making any API call.
Standard per-call/per-job logging already covers the mechanical details;
this is specifically so an anomalous *section* of the log/queue history has
an attributable cause, without relying on anyone's memory of a terminal
session.

**Why:** Dave, s48/49: investigating why the pipeline was "burning tokens
and not really doing any work toward listing items," found
`scripts/requeue_ebay_draft_402_dead_letters.py` had created 6,607 requeue
jobs against a documented expectation of ~2,689 — it had been run more than
once, silently, because it has no logging beyond `print()` to a terminal
that's long gone. Nothing in the durable record said "this script ran, at
this time, with these args" — the only reason it was traceable at all is
that the script happens to stamp `bulk_requeue_reason`/`retried_from_job`
onto each job's payload, which won't be true of every future one-off
script. Dave: "one off scripts should definitely announce what they are up
to... it would help to know why an anomalous log entry section was
occurring."

**Enforcement:** `tgw.logging.announce_script_run()` added
(`src/tgw/logging.py`) — emits a `script_run_start` event via the existing
`log_event` machinery. **Not yet retrofitted** onto existing scripts under
`scripts/`, including `requeue_ebay_draft_402_dead_letters.py` itself
(left unrun, per Dave — no reason to touch it further right now). **No
automated detector yet** — nothing currently fails a script that skips the
announce call. Todo #1250 tracks both: audit `scripts/` for one-off tooling
missing this, and decide whether to add a grep-based CI/catalog-verify
check that flags a `scripts/*.py` file with a `main()` but no
`announce_script_run` call.

**Enforcement:** `llm.py call_model()` gates the openrouter→google_direct
fallback on `quota.context_kind() == 'interactive'`; the google_direct path
is `quota.precheck('llm_google')`-gated (post-429 stand-down / circuit
breaker); `quota._DEFAULT_BUDGETS['llm_google'] = 20` halts background
callers at the threshold and surfaces spend in the `quota` health check.
Tests: `tests/test_llm_google_direct.py`
(TestOperatorEmergencyReserve, TestGoogleStandDown),
`tests/test_quota.py::TestEnforcement::test_llm_google_default_budget_halts_background`.

## E10 — Every host's flake checkout stays in sync with `origin/master` ⚠️ (2026-07-16, incident)

**Rule:** No host's local `~/tgw-flake` checkout may sit ahead of or diverged from
`origin/master` for an extended period without it being surfaced. A host-local commit is
fine as a transient working state; it becomes a violation once it persists unpushed/
unpulled across sessions with nothing watching for it.

**Why:** Dave, 2026-07-16 (`INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md`):
a1131's local flake checkout had accumulated **15 commits** ahead of `origin/master` —
including 2 (`ae13f50`, `61e9a3f`, todo #1427) that existed nowhere else — for an unknown
period, silently. tgw-prod's own checkout was independently 2 commits ahead
(`b60508a`/`7121049`) with no relation to a1131's drift. Neither divergence was visible
until a live incident forced a manual `git log`/`merge-base` comparison across both hosts.
This is a structural gap, not an agent-discipline gap alone: even a perfectly disciplined
session working on one host in isolation will eventually hit this if nothing external ever
forces a push/pull or alerts on the gap.

**Enforcement:**
- `nix-flake-maintainer` agent (`.claude/agents/nix-flake-maintainer.md`) treats "diff
  every known host's checkout against `origin/master` and against each other" as its
  mandatory first step before any flake mutation — not conditional on suspicion.
- **Gap, not yet built:** a standing, agent-independent detector (cron/systemd-timer script,
  or folded into Tigwa's existing scheduled plan-review cadence) that checks each known
  host's `HEAD` against `origin/master` on its own schedule and surfaces drift past a small
  commit threshold, the same way `thermal.status` is checked independent of any session
  choosing to look. Tracked under PP-AGENT-DISCIPLINE-001, todo #1444's follow-up (not
  filed as its own todo yet — file one before considering this invariant ✅).

## E11 — An agent's role restrictions are locked in by tool permissions and hooks, not by its own system-prompt prose ✅ (2026-07-16, PP-AGENT-DISCIPLINE-001; both gaps below closed 2026-07-18)

**Rule:** every "must"/"never" rule in a `.claude/agents/*.md` profile is a candidate for
mechanical enforcement (scoped `tools:`, a `PreToolUse`/`SessionStart` hook, or a harness
feature like worktree isolation) before it is trusted as prose alone. Prose-only rules are
acceptable only where no mechanical equivalent exists yet — and that gap should be named
explicitly, not silently assumed covered by "the agent will read and follow it."

**Why:** the same session that built E10/the PreToolUse flake-guard hook (this same day)
skipped CLAUDE.md's own startup ritual again hours later — a written instruction, restated
even more forcefully, still depends on the model choosing to comply, and that already
failed twice. Dave's generalization, same day: "we should use a similar pattern when
configuring any agent to lock them into their role" — the CLAUDE.md fix (a `SessionStart`
hook, see below) is one instance of a pattern that applies to every custom agent profile in
`.claude/agents/`, not just the main session.

**Enforcement, by example (audited 2026-07-16, both remaining gaps closed 2026-07-18 per
todo #1389/#1450):**
- ✅ CLAUDE.md's own startup ritual — `SessionStart` hook
  (`.claude/hooks/session-start-briefing.py`) now injects inbox/suggestions/plan-check state
  automatically; no longer depends on the model noticing it should look.
- ✅ `nix-flake-maintainer`'s "narrow, procedure-gated" mutation list — `flake-guard.py`
  (`.claude/hooks/flake-guard.py`) now covers both Bash (`nixos-rebuild switch`/`test`,
  `git commit`/`push` naming `tgw-flake`) AND raw `Edit`/`Write` tool calls landing directly
  on a file inside `~/tgw-flake` (matcher extended to `Bash|Edit|Write`).
- ✅ `tgw-coder`'s worktree-isolation contract (`.claude/agents/tgw-coder.md` §2) — a
  dedicated `PreToolUse` hook, `.claude/hooks/worktree-guard.py` (matcher `Edit|Write`),
  mechanically blocks any Edit/Write outside `/opt/TGW/var/worktrees/<id>-<slug>` or
  `/home/db/tgw-worktrees/<id>-<slug>` when `agent_type == "tgw-coder"` — including a
  dedicated check for the harness's own auto-provisioned `.claude/worktrees/agent-<id>/`
  path (the exact conflict #1450 found live). Companion change:
  `settings.worktree.bgIsolation: "none"` so the harness's own competing background-
  isolation mechanism never auto-provisions a second worktree underneath the agent.

## E12 — Live-troubleshooting sessions diagnose freely but execute application-code fixes through tgw-coder, not direct edits ✅ (2026-07-18, Dave)

**Rule:** the main session (and any non-`tgw-coder` agent) may read, grep, and reason about
`src/tgw/`/`tests/` freely during root-cause diagnosis — that part is inherently exploratory
and can't be packet-scoped before the root cause is known. But the actual code *change*, once
a fix is scoped, is a todo/packet dispatched to `tgw-coder`'s isolated worktree+branch — the
same split nix-flake-maintainer already enforces for `~/tgw-flake` (diagnose live, but every
gated mutation goes through the agent's procedure).

**Why:** Dave, 2026-07-18, after a multi-session troubleshooting run (root-causing a stale
inventory badge, then landing all 4 packets of PP-CATALOG-INCR-001) happened entirely as
direct `Edit` calls on `http_server.py`/`items.py`/`sqlite_catalog.py`/`state_machine.py`
and several test files in the shared checkout, never handed to `tgw-coder`: "we seem to be
running these fixes outside our new process. How can we funnel these troubleshooting type
sessions through a similar process to what we did in the sprint... make both something you
recognize and something that can be specified directly like the nix maintainer?" Confidence
in the flake process specifically comes from `flake-guard.py` existing and firing — the
mechanical nudge, not just the doc saying to use the agent. This invariant is the same fix
applied to the other half of the codebase.

**Enforcement:** `.claude/hooks/app-code-guard.py` (new, 2026-07-18), `PreToolUse` on
`Edit|Write`, mirrors `flake-guard.py`'s pattern exactly: any Edit/Write under `src/tgw/` or
`tests/` where `agent_type != "tgw-coder"` triggers `permissionDecision: "ask"`, naming the
tgw-coder dispatch path as the expected route. Registered in `.claude/settings.json`'s
`PreToolUse` list alongside `flake-guard.py`/`worktree-guard.py`.

**Known gap, same as `worktree-guard.py`'s:** Edit/Write only, not Bash — a `sed -i`/`tee`
against a guarded path bypasses this hook. Not yet built; flag if it becomes a real bypass
pattern rather than a theoretical one.

**Live-fire not yet confirmed:** per `reference-hooks-settings-watcher-caveat` — this repo's
settings watcher only picks up a hooks config that existed when the session started, so a
`/hooks` reload or session restart is needed once before this is proven firing for real, same
open item as the `SessionStart` briefing hook and the original flake-guard/worktree-guard
hooks had when first added.

## E13 — A relayed request needs verified provenance before it's treated as Dave's own direction ⚠️ (2026-07-19, Dave — OPEN, no detector yet)

**Rule:** `inbox/tigwa/*-REQUEST-*.md` / `DAVE-REQUEST-*.md` files (Tigwa recording what she
understood Dave to want) are genuinely valuable — confirmed 2026-07-19 as an already-working,
in-production instance of PP-OUTBOX-001's translation concept (see that PP's §3 update). But
"well-formed and sitting in the expected inbox location" is not the same as "verified to
actually be Dave's direction." Right now nothing distinguishes a genuine one from a
mistaken, hallucinated, or (worst case) forged one — the same relay-authorization risk this
session already enforced hard against for subagents (`feedback-relayed-authorization-never-
trusted`), now surfacing on the Claude-reads-Tigwa's-inbox-notes side instead of the
agent-accepts-orchestrator's-claim side.

**Why:** Dave, immediately after endorsing Tigwa's requests as "my prompts now": "we still
need some level of verification to guard against false request injection." He raised this
himself, unprompted, the moment the trust question became concrete — not a hypothetical
Claude is inventing, a named requirement from Dave that isn't answered yet.

**Status: genuinely open, no design yet.** Open questions for whoever picks this up:
- Does every Tigwa-authored request need Dave's explicit countersignature before Claude acts
  on it, or only above some risk/consequence tier (matches PP-OUTBOX-001's `delivery_boundary`
  field concept — inspect-only vs. side-effecting)?
- What's the actual verification mechanism — Dave replying/confirming in `inbox/dave/`, a
  simple shared-secret/signing step, or something else? No mechanism has been proposed yet,
  only the requirement.
- Does this apply retroactively to judging past Tigwa requests as trustworthy, or only
  forward from here?

**Architectural link, Dave 2026-07-19, same session, right after E13 was filed:** "the
verification is likely tied to the cryptographically locked execution plan" — i.e. this is
almost certainly not a bespoke mechanism to design from scratch. It's a specific instance of
`pp/PP-CATIONIX-001.md`'s already-designed **crypto-lock** (endgame, not yet built): "a
Securix-style cryptographic per-worker execution token: signed policy lockfile + short-TTL
unlock artifact + worker attestation + audit trail." A Tigwa-authored request that needs to
authorize a consequential action would need a valid, Dave-signed unlock artifact attached
(or referenced) before Claude/an agent treats it as executable — a forged/hallucinated
request simply couldn't produce one. **Don't design E13 as a standalone provenance scheme;
fold it into the crypto-lock design when that's built** (tracked as a future addendum to
PP-AIOPS-001 Phase 5/6 per PP-CATIONIX-001.md's own note — same gating point, not a second
mechanism). Until the crypto-lock exists, E13 stays open and unenforced by construction —
this is expected, not a gap to rush a workaround for.

**Enforcement: none yet.** No detector, no hook, no gate. Treat this the same as any other
named-but-undesigned gap in this file (e.g. #1459's credential-scoping) — don't silently
build a fix without a real design pass, and don't let it sit only in conversation history;
this entry is that durable record. See `pp/PP-OUTBOX-001.md` and `pp/PP-CATIONIX-001.md` for
where the eventual design should land.

## E14 — Agent-trace evidence is write-once/append-only for every agent, no exemptions ✅ (2026-07-20, Dave, PP-AGENTTRACE-001)

**Rule:** no agent — including the one that created a given trace record, including `Claude`
itself, no exceptions by role or trust level — may modify or delete an archived transcript
file (`/opt/TGW/var/agent-traces/`), an `agent_runs` row, or a hash-commitment row (packet
#1586) once written. The only sanctioned write path is `tgw trace start`/`tgw trace end` and
`tgw.logging.archive_transcript()`'s own insert-only/lock-on-first-archival logic. This
closes the exact self-attestation/cover-up vector Tigwa's review (todo #1585) named: a
compromised or runaway agent altering its own evidence trail after the fact.

**Why:** Dave, 2026-07-20, immediately after the Leg A/B integrity-hardening design (packet
#1586) was drafted: "this needs to be baked into everyone's contract. No touching the traces
or you self-destruct." Read for severity, not mechanism — this invariant is the actual
enforcement (a hard `deny`), not a literal self-destruct routine, which this project's own
doctrine (Prime Directive 2/3, no self-devised safety bypasses) wouldn't sanction building
regardless of instruction framing. Same standing doctrine as E9/E11/E12: a written rule
depends on the model choosing to comply every time; a hook doesn't ask.

**Enforcement:** `.claude/hooks/trace-immutability-guard.py` (new, 2026-07-20), `PreToolUse`
on `Bash|Edit|Write`, registered in `.claude/settings.json` alongside `flake-guard.py`/
`app-code-guard.py`/`worktree-guard.py`. Unlike those three, this hook has **no exempt
agent** — every agent identity is guarded equally, since there is no legitimate agent whose
contract is to directly mutate trace evidence (the CLI/`archive_transcript()` write path
itself never goes through Edit/Write or a matching destructive Bash pattern, so legitimate
recording is unaffected). Blocks: `Edit`/`Write` on any path under `/opt/TGW/var/agent-
traces/`; `Bash` commands combining a destructive verb (`rm`/`mv`/`cp -f`/`dd`/`shred`/
`truncate`/`chmod`/`chown`/output-redirect/`tee`/`sed -i`) with that path in the command
string; `Bash` commands containing `UPDATE`/`DELETE FROM`/`DROP TABLE`/`TRUNCATE` against
`agent_runs` or `agent_run_transcript_hashes`.

**Known gap, same transparency convention as the other hooks' admitted Bash gaps:**
best-effort pattern match on the Bash command string — a sufficiently obfuscated command, or
DB/filesystem access this hook's regexes don't recognize, is a real, documented limitation,
not claimed coverage the hook doesn't have.

**Interim mechanism, not the permanent one (Dave, same session): "eventually this will be
enforced by our crypto environment watcher."** Same pattern as E13's own note — don't treat
this hook as the final design. It's the mechanical stopgap until `pp/PP-CATIONIX-001.md`'s
crypto-lock endgame (signed policy lockfile + short-TTL unlock artifact + worker attestation
+ audit trail) subsumes it with a real cryptographic guarantee instead of a pattern-matched
`PreToolUse` hook. When that lands, fold this invariant's enforcement into it rather than
maintaining two parallel mechanisms.

**Live-fire not yet confirmed:** per `reference-hooks-settings-watcher-caveat` — needs a
`/hooks` reload or session restart before this is proven firing for real, same open item as
every other hook in this file when first added.

---

## E16 — Every queue job declares a complete manifest (dedupe_key, entity_id for per-item work); `enqueue_job()` is the enforcer, not a passive audit ✅ (2026-07-20, todo #1608, PP-STATEMACHINE-001)

**Rule:** every call to `state_machine.enqueue_job()` must supply a non-empty `dedupe_key`
(reject semantics by default; `debounce=True` extend semantics for singleton/batch-coalescing
triggers) unless it explicitly passes `dedupe_key_exempt=True` — a deliberate, reviewed,
code-commented exception, never a silent way to dodge the contract. Any call with
`entity_type='item'` must supply a non-empty `entity_id` — no opt-out for this one; a
per-item job's `entity_id` is always knowable at the call site. `enqueue_job()` raises
`state_machine.MissingManifestFieldError` (a `ValueError` subclass) at call time when either
requirement isn't met, instead of silently accepting an incomplete job the way it did before
this packet. `priority` is required in effect but config-defaultable: an omitted `priority=`
resolves via `resolve_priority(queue_name, operation)` against
`/opt/TGW/config/tgw-queue-priorities.json` (same `defaults`/`use_default` shape/convention as
`tgw-models.json`), falling back to 100 (`normal`) if no entry exists — an unmapped queue is
not an error. `supersede=True` is the manifest's "force this job eligible right now" property:
atomically cancels any existing non-terminal row under the same `dedupe_key` before inserting
the fresh one, for the case `debounce=True`'s `GREATEST(not_before)` collision handling can't
express (it can only push a pending job's schedule *later*, never earlier).

**Why:** surfaced during PP-SOLD-001's #1604/#1605/#1607 incident chain (multi-order
data-loss bug, `ebay_legacy_sync` restore, lease-expiry race, 451-row duplicate-job
backlog). The #1607 audit found the same missing-`dedupe_key` hole in **8** self-rescheduling
workers (`ebay_legacy_sync`, `ebay_sync`, `token_refresh`, `velocity_stats`,
`ebay_price_reducer`, `ebay_sku_migrate`, `ebay_dole`, `sync_conflict`) — not the 5 originally
suspected — plus `ebay_upload.py`'s quota-retry path and two `ebay_sync` per-sku manual
trigger sites in `http_server.py` that needed their own key distinct from the singleton's.
Dave, 2026-07-20, after the audit: "not missing, just missed by the second coder" — the fix
had to be structural, not a per-call-site patch the next new worker could just as easily
skip. "This is state machine, not job contracts... we can call it and handle it as a manifest
but the state machine is the enforcer." Same shape of risk `dedupe_key` already had before
E-numbered enforcement: a rule that lived only in a docstring warning
(`enqueue_job()`'s own `entity_id` paragraph pre-dates this packet and had already shipped a
~300k-row breakage once, todo #1406/PP-DEADLETTER-001, before this same enforcement pattern
closed that hole too).

**Enforcement:** `state_machine.enqueue_job()` itself, in `src/tgw/queue/state_machine.py` —
no external hook, no separate validation layer, no dependency on the Claude Code
harness-hook-firing bug found the same session (todo #1531, E11/E12's known gap). It fires on
every call, unconditionally, by construction — the same reasoning the PP doc gives for not
building this as a bolted-on layer: `state_machine` is already this codebase's established
name for the whole queue/job subsystem (the Postgres database is literally named
`state_machine`).

**Sequencing (why this shipped as one packet, all 4 phases, not enforcement-first):** flipping
`enqueue_job()`'s enforcement on before every real call site had a `dedupe_key` would have
broken `ebay_sync`/`ebay_legacy_sync`'s live production loops immediately. Order followed:
(1) fix all 15+ call sites the #1607 audit found — 8 workers × 2 sites (startup enqueue +
`_reschedule()`, sharing one `'<queue_name>:pending'` debounce key), `ebay_upload.py:186`'s
quota-retry (`dedupe_key=f'ebay_upload:{sku}'`, reject semantics), and `http_server.py`'s two
per-sku `ebay_sync` manual-trigger sites (`f'ebay_sync:sku:{sku}'`, deliberately distinct from
the singleton's `'ebay_sync:pending'` key so one queue_name's two job shapes don't collide);
(2) ship `tgw-queue-priorities.json` + `resolve_priority()`; (3) ship `supersede` + wire `tgw
restart-ebay-token` to it (the exact "force now regardless of pending schedule" need #1607
flagged as item D.1); (4) only then flip the `MissingManifestFieldError` raise on, after a
full fresh codebase-wide AST re-grep (not just trusting the original #1607 audit) confirmed
every real `enqueue_job()` call site — src/tgw/ and scripts/ — already supplied a `dedupe_key`,
zero holdouts found, `dedupe_key_exempt=True` shipped but unused as of this packet landing.

**Known gap, not yet built:** `tgw-queue-priorities.json` was authored and reviewed as part of
this packet but is a live-only deploy artifact (like `tgw-models.json`, not git-tracked) —
`tgw-coder`'s worktree-isolation contract means it couldn't be landed directly into
`/opt/TGW/config/` from the branch; the reviewed content sits at
`docs/TGW-Plan-Vault/plan/packets/results/1608-tgw-queue-priorities.json` pending a live-deploy
step (same category as the worker restarts this packet also defers to Dave/Claude,
post-review). `resolve_priority()` tolerates the file being absent — every lookup just falls
back to `normal` (100, today's already-implicit default) until it's deployed, so nothing
breaks in the interim.
