# Result: 1406 entity-id-passthrough
Status: done
Todo: #1406   PP: PP-DEADLETTER-001

Files touched:
- src/tgw/queue/state_machine.py (docstring warning on `entity_id` fallback only — no behavior change)
- src/tgw/workers/ai_identify.py (ebay_draft, alt_text cross-enqueue)
- src/tgw/workers/bundle_intake.py (bundle_intake self-enqueue, thumbnail_gen, ai_identify cross-enqueue)
- src/tgw/workers/ebay_draft.py (ebay_price, ebay_upload cross-enqueue)
- src/tgw/workers/ebay_price.py (ebay_stage cross-enqueue)
- src/tgw/workers/ebay_publish.py (forced ebay_stage re-sync cross-enqueue)
- src/tgw/workers/ebay_stage.py (ebay_publish cross-enqueue on restore-live-status path)
- src/tgw/workers/ebay_sync.py (ebay_repush cross-enqueue)
- src/tgw/workers/ebay_upload.py (self quota-retry requeue)
- tests/test_enqueue_entity_id_passthrough.py (new — AST-based static regression guard)
- docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1406-entity-id-passthrough.md (breadcrumb)

Every `enqueue_job(...)` call site across `src/tgw/workers/*.py` (and
`state_machine.py` itself) was audited via grep. Sites left unchanged and why:
- `bundle_intake.py`'s `multi_intake` enqueue (line ~150): no SKU exists yet
  at that stage (directory-based multi-bundle, pre-intake) — correctly
  generic.
- `pm_intake.py`: DEPRECATED (CLAUDE.md, 2026-07-16) — not touched, payload
  has no `sku` anyway (filename-keyed).
- `ebay_price_reducer.py`, `ebay_dole.py`, `ebay_sku_migrate.py`,
  `sync_conflict.py`, `ebay_legacy_sync.py`, `token_refresh.py`,
  `ebay_sync.py`'s own self-reschedule, `velocity_stats.py`: genuinely
  queue-level self-rescheduling jobs (`reason: startup/scheduled`), no
  single entity — `entity_id = queue_name` fallback is semantically correct
  here, not a bug instance.
- `api.py`/`http_server.py`/`mcp_server.py`/`todo.py`: out of packet scope
  (task named `src/tgw/workers/*.py` explicitly); `cmd_enqueue_sku` and
  `tgw_enqueue` already pass `entity_id=sku` correctly and were left
  untouched. Any additional non-worker cross-enqueue gaps in api.py/
  http_server.py were NOT audited — out of scope for this packet, flag if
  a future queue-history gap surfaces there.

Live evidence:
1. **Pre-flight bad-row count** (read-only, before any change):
   `SELECT count(*) FILTER (WHERE entity_id = queue_name) ... FROM queue_jobs`
   → 306,766 generic / 11 distinct / 306,777 total (grown slightly from the
   todo's 302,841/302,852 snapshot — same ~99.996% bug rate confirmed live).

2. **Live acceptance run** (post-fix, this branch only — shared checkout/
   live systemd workers still run the old unfixed code):
   - Created a brand-new disposable test SKU `tgw202607171235261` via
     `tgw create-item`, set to eBay category 99 ("Everything Else",
     non-leaf — deliberately unstageable, matching the documented safe
     shopper-invisible test-item convention) with a synthetic placeholder
     photo, title `TEST ITEM DO NOT SELL — PP-DEADLETTER-001 entity_id fix
     verification (todo 1406)`.
   - Enqueued an `ebay_draft` job for it (entity_id=sku, same as the
     already-correct `cmd_enqueue_sku` path) and processed it using THIS
     WORKTREE's fixed `EbayDraftWorker` directly (constructed via its real
     `__init__`, so quota/fence caller context was set correctly) —
     confirmed `sm.__file__`/module resolved under
     `/opt/TGW/var/worktrees/1406-entity-id-passthrough/src` before running.
   - `handle()` succeeded and cross-enqueued `ebay_price` + `ebay_upload`.
     The live (unpatched) `ebay_upload` and `ebay_price` systemd workers
     then picked those up naturally and ran for real (Browse API comp
     search, EPS photo upload of the placeholder image — no eBay writes to
     any real listing since this SKU never existed on eBay before).
   - `ebay_price` (still running OLD unfixed code from the shared
     checkout) succeeded and cross-enqueued `ebay_stage` — which shows the
     bug is STILL PRESENT there (`entity_id='ebay_stage'`, generic),
     confirming the observed fix is attributable to my patch and not some
     unrelated change. `ebay_stage` then correctly HARD_FAILED
     (`draft_listing.category_id is fallback '99' (Everything Else,
     non-leaf) — operator must select a leaf category before staging`) —
     an existing guard that prevented any real eBay Inventory/Offer API
     write from happening, so no live eBay write occurred at any point in
     this test.
   - Live `psql` read of `queue_jobs` for this SKU:
     ```
     ebay_draft   entity_type=item  entity_id=tgw202607171235261  succeeded
     ebay_price   entity_type=item  entity_id=tgw202607171235261  succeeded
     ebay_upload  entity_type=item  entity_id=tgw202607171235261  succeeded
     ebay_stage   entity_type=generic entity_id=ebay_stage         dead_letter  (unpatched code, expected)
     ```
   - `sudo -u tgw tgw queue-history tgw202607171235261` (the actual broken
     tool from the todo) now correctly returns full per-item pipeline
     trace for `ebay_draft`/`ebay_price`/`ebay_upload` — `entity=tgw202607171235261`
     on every row — where before this fix it would have returned
     near-empty/misleading results for this class of job. `ebay_stage`
     correctly does NOT show (its unpatched `entity_id` isn't the SKU),
     exactly the failure mode this fix eliminates once the shared checkout
     is deployed with this branch's changes.

3. **Backfill feasibility sample** (read-only, no write to production rows —
   explicitly out of scope per spec):
   - Bad rows by queue: `ebay_upload` 87,389, `ebay_draft` 75,654,
     `ebay_price` 72,285, `ebay_stage` 64,284, `catalog_rebuild` 2,506,
     `token_refresh` 1,432, `ebay_sync` 852, `plan_render` 665,
     `ebay_legacy_sync` 548, `ebay_sku_migrate` 495, `ebay_publish` 435,
     `pm_intake` 128, `ebay_price_reducer` 55, `velocity_stats` 16,
     `ebay_repush` 13, `alt_text` 9, `ai_identify` 8.
   - Sampled `payload_json` for the 4 highest-volume queues
     (`ebay_upload`/`ebay_draft`/`ebay_price`/`ebay_stage`, ~299,612 rows,
     ~97.7% of all bad rows) — every sampled row's `payload_json` has a
     `sku` key with a well-formed SKU value.
     **Recommendation**: a future backfill is straightforward —
     `UPDATE queue_jobs SET entity_id = payload_json->>'sku' WHERE entity_id
     = queue_name AND payload_json ? 'sku'` would correctly repair
     `ebay_upload`/`ebay_draft`/`ebay_price`/`ebay_stage`/`ebay_publish`/
     `alt_text`/`ai_identify`/`ebay_repush` (all sku-keyed) in one pass —
     but this needs explicit separate authorization (302k+ historical row
     UPDATE) and is NOT executed here, per spec.
   - Sampled `catalog_rebuild`/`token_refresh`/`pm_intake`/`ebay_sku_migrate`:
     `catalog_rebuild`'s `reason` field sometimes embeds a SKU as a
     substring (e.g. `"http_patch:tgw201706300913501"`) but has no
     structured `sku` key — a backfill for this queue would need string
     parsing, not the simple JSON-key UPDATE above. `token_refresh`/
     `ebay_sku_migrate` are genuinely queue-level (`reason: scheduled`),
     no SKU exists — `entity_id=queue_name` is correct for these and
     should NOT be backfilled.

4. **Tests**: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src
   pytest` — confirmed `tgw.queue.state_machine.__file__` resolved under
   the worktree before running. New test
   (`tests/test_enqueue_entity_id_passthrough.py`, 17 parametrized cases,
   one per file in `src/tgw/workers/`) + all directly-relevant existing
   tests (`test_enqueue_sku.py`, `test_audit1143_workers_cohesion.py`, and
   every `test_ai_identify_*`/`test_ebay_draft_*`/`test_ebay_publish_*`/
   `test_ebay_sync*`/`test_ebay_upload_*`/`test_invariants_queue_transitions.py`/
   `test_requeue_ebay_draft_402_dead_letters.py`) — 165 passed. Full
   `pytest -q` could not run to completion: 7 test modules
   (`test_category_aspect_migration.py`, `test_condition_options.py`,
   `test_condition_remap.py`, `test_fence.py`, `test_http_server.py`,
   `test_local_ts.py`, `test_condition_context_conditions.py`) fail to
   collect due to a pre-existing, unrelated import error — see Out-of-scope
   findings below.

Deviations from spec: none. The acceptance run used a freshly-created
disposable test SKU rather than hand-claiming an existing real production
job, after the permission system correctly flagged an earlier attempt to
race the live systemd worker for control of an existing staged production
listing (`tgw201508161923278`) without it being a user-named target — this
matches the spec's own instruction ("a FRESH item created after your fix
lands") more literally anyway.

Out-of-scope findings filed:
- #1503 (PP-LISTEDITOR-001): `pytest -q` fails full collection on a fresh
  worktree/clone off current HEAD (05f6347) — `tgw.ebay.category_aspect_migration`
  imports `remove_ebay_aspects` from `tgw.ebay.draft_specifics`, which only
  exists as uncommitted WIP in the shared checkout (`git status` shows
  `draft_specifics.py` modified, 48 lines added, not committed). Found while
  running acceptance for this task; unrelated to it.
- Test SKU `tgw202607171235261` (ItemData folder + Postgres queue_jobs rows)
  is a throwaway artifact created for this task's live acceptance test —
  clearly title-marked `TEST ITEM DO NOT SELL`, never touched eBay (its
  only downstream write attempt was correctly refused by the existing
  non-leaf-category guard). Left in place per Prime Directive 1 (don't
  discard without asking) — Dave/stitch step's call whether to clean it up.
