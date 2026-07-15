# Result: 1400 single-sku-cascade-apikey-imagelinks
Status: done
Todo: #1400 (covers #1396)   PP: PP-DEADLETTER-001

Files touched:
- tests/test_config_hygiene.py (regression tests added, no source fix needed)

Live evidence:
- All 4 dead-letters for SKU `tgw202605051933258` traced via
  `queue_jobs` timestamps against git history:
  - `ebay_stage` KeyError('api_key') — created 2026-06-30 00:58:19 UTC
  - `ebay_upload` KeyError('api_key') — created 2026-06-30 00:58:26 UTC
  - `ebay_stage` HardFailure ImageLinks x2 — created 2026-06-30 01:14:45
    and 01:15:32 UTC
  - **All 4 predate commit `00cf9274` ("feat: PP-PROMO-001 P3+P4 ...
    ebay_upload filter fix"), committed same day at 07:57:36 UTC**, which
    made TWO relevant fixes in the same diff:
    1. `src/tgw/config.py`: before this commit, `load_config()`'s returned
       dict had **no `api_key` key at all** unless
       `secrets_root/tgw-api-key.json` happened to exist at load time (the
       key was only inserted inside `if _api_key_path.exists(): ...`).
       `tgw.apis.fence._headers()` does `cfg['api_key']` unconditionally —
       any fence call made with a cfg loaded before the key existed (or
       transiently missing) hit `KeyError('api_key')`. The fix moved
       `"api_key": _api_key` to always be present in the returned dict
       (empty string as the safe default), gated only on the *value*, not
       key presence.
    2. `src/tgw/ebay/sync.py` + `src/tgw/workers/ebay_stage.py`: same
       commit added the `image_urls[:24]  # eBay max is 24 images per
       listing` cap in both places. This SKU had 25 uploaded `ebay_photos`
       at dead-letter time (uncapped), producing eBay's malformed
       "ImageLinks cannot exceed . is an invalid attribute" rejection
       (the template's max-count value came back empty from an
       over-length submission).
  - **Confirmed live today** (`fence.get_item()` against the real fence):
    `ebay_offer.offer_id=264653924018`, `ebay_offer.status=PUBLISHED`,
    `ebay_listing.status=PUBLISHED`, `ebay_photos` count=25 (all uploaded,
    unchanged since), `draft_listing.imageUrls` count=24 (capped, matches
    the fix), `pipeline_error=None`. The item successfully re-staged and
    published multiple times after 00cf9274 landed (queue_jobs shows
    successful `ebay_stage`/`ebay_upload`/`ebay_publish` runs from
    2026-06-30 01:13 onward, including an operator force-update on
    2026-07-05). Both bugs are fixed and this item is healthy.
- Investigated the "entity_id = queue_name" anomaly the packet flagged as
  a possible root-cause thread: **not specific to this SKU or to
  api_key/ImageLinks at all** — `state_machine.enqueue_job()`'s
  `entity_id` kwarg defaults to `queue_name` when not explicitly passed
  (`entity_id = entity_id or queue_name`, state_machine.py:141), and
  almost no pipeline-internal cross-enqueue call site passes it. Live
  query: 302841/302852 `queue_jobs` rows system-wide (99.996%) have
  `entity_type='generic'`/`entity_id=queue_name`; only 7 rows (all via
  `tgw_enqueue`/`cmd_enqueue_sku`, the MCP/CLI manual-trigger paths) carry
  a real `entity_id=sku`. This is universal default behavior, not an
  anomaly isolated to this batch — ruled out as a contributing cause of
  the api_key/ImageLinks bugs. Filed as its own finding: todo #1406.
- `pytest -q` (worktree copy verified via `tgw.config.__file__` under
  `/opt/TGW/var/worktrees/1400-cascade-apikey-imagelinks/...`,
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH`): **2213 passed, 1 skipped, 0
  failed** (351.7s). New tests
  (`test_api_key_present_when_secret_file_missing`,
  `test_api_key_loaded_when_secret_file_present`,
  `test_api_key_present_when_secret_file_malformed`) pass and pin the
  invariant so this class can't silently regress.
- `tgw health`: same 4 pre-existing warn-level failures as before this
  session (`backups` — rclone stamp stale, unrelated infra; `nats` —
  `No module named 'nats'`, pre-existing dep gap; `ebay_sync_fallback` —
  492 consecutive fallback runs, tracked separately at todo #1077;
  `quota` — normal daily usage/429 tracking, not an error state). None
  relate to config/fence/api_key; no new failure introduced (this task
  made no source changes, only added tests).

Deviations from spec:
- Spec step 2 said "determine why it constructs/passes a config dict
  missing `api_key` — fix at the source." Investigation found the actual
  cause was **not** a caller constructing a bad dict — it was
  `load_config()` itself omitting the `api_key` key entirely under
  certain conditions, on any/every cfg built before commit `00cf9274`
  (2026-06-30, predates this session by two weeks). That commit already
  fixed both underlying bugs (api_key key-presence and the ImageLinks
  24-image cap) the same day these jobs dead-lettered, several hours
  after. No source fix was needed or made — only regression tests to pin
  the now-correct behavior, per Acceptance item 1's "add a regression
  test covering that path with a normal, complete cfg." This is flagged
  explicitly rather than manufacturing a new source change against
  already-fixed code.
- These 4 dead-letter rows themselves remain in `dead_letter` state in
  `queue_jobs` (visible in `tgw health`'s `ebay_stage`/`ebay_upload`
  dead-letter counts) — out of scope to clear/requeue per this packet
  (no live/production write beyond acceptance was authorized, and the
  item itself is already healthy/published, so nothing needs re-running
  for this SKU). Leaving the historical rows in place for triage-batch
  bookkeeping; PP-DEADLETTER-001's owning process can decide whether to
  mark them reviewed/closed.

Out-of-scope findings filed: #1406 (queue_jobs.entity_id defaults to
queue_name for ~99.997% of rows — systemic, not #1400-specific; filed
under PP-DEADLETTER-001)
