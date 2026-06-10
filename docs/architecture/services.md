# TGW Services & Subsystems

**Status:** living document. Created 2026-06-10. Companion to
[overview.md](overview.md). Each entry documents: responsibility, inputs/outputs,
dependencies, data stores, external APIs, failure modes, and critical invariants.
Anything not verifiable from code/config is marked **ASSUMPTION**.

Conventions used below:
- *ItemData* = `/opt/TGW/data/ItemData/<SKU>/<SKU>.json` + media (canonical item store).
- *Ledger* = PostgreSQL db `state_machine`, table `queue_jobs`.
- *cfg* = dict returned by `tgw.config.load_config()` from `/opt/TGW/config/tgw-api-config.json`.
- All queue workers inherit the failure modes and invariants of the **Queue state machine**
  section; per-worker sections list only what is specific to them.

---

## 1. Platform layer — `tgw` core library + CLI (`tgw-api`)

- **Source:** `src/tgw/{config,resolver,items,catalog,sqlite_catalog,catalog_export,api,
  health,logging,notify,todo,velocity,listing_quality,fingerprint,clip,scrub,thumbnail}.py`
- **Responsibility:** the *fence*. Owns all ItemData path construction, JSON read/write,
  selector resolution (`resolve()`), catalog builds, health checks, and the `tgw` CLI
  (~100 subcommands in `api.py`). Every other component (workers, HTTP, MCP, shell wrappers)
  calls into this layer rather than touching files.
- **Inputs:** CLI argv; cfg; ItemData JSON; SQLite catalog (for reads); Ledger (for queue
  subcommands and todo).
- **Outputs:** exactly one JSON object per CLI call (`{"ok": ...}` contract); mutated
  ItemData JSON; enqueued jobs (e.g. `tgw publish`, `tgw bulk --apply`, `tgw enqueue-sku`);
  derived catalogs (`tgw build-all`).
- **Dependencies:** PostgreSQL (only for queue/todo/health subcommands — item operations work
  without it); Pillow optional (thumbnails/fingerprint).
- **Data stores:** ItemData (read/write), SQLite catalog (read, and write during builds),
  Ledger (read/write), `todo_items` table, clip SQLite store.
- **External APIs:** none directly for core item ops; some subcommands call eBay
  (`tgw publish`, `tgw ebay-pull`, `tgw reprice-suggest`) — see eBay sections.
- **Failure modes:**
  - Concurrent writers to the same SKU JSON — the fence serializes within one process but
    there is **no cross-process file lock** (**ASSUMPTION:** last-writer-wins on rare
    collisions; workers are partitioned by pipeline stage so overlap is unusual).
  - SQLite catalog staleness between a write and the coalesced rebuild (≤ ~30 s + build
    time) — list/search reads can be stale; full item GET reads the JSON so it never is.
  - `load_config()` silently prefers code defaults over some JSON values (ISS-003).
- **Critical invariants:**
  - No code outside this layer constructs `ItemData/...` paths.
  - `sku` is immutable through every API (CLI, HTTP PATCH rejects it).
  - Any field write clears `catalog_verified` (hall-pass invalidation, PP-VERIFY-001).
  - Writers enqueue `catalog_rebuild`; never call `build_all_catalogs()` inline.
  - Output contract: one JSON object with `ok` on stdout, always.

## 2. Queue state machine + worker runtime

- **Source:** `src/tgw/queue/{schema.sql,state_machine.py,worker_base.py,ollama_lock.py,
  __init__.py}`; systemd `tgw-worker@.service` template + `queue-workers.target` +
  `queue-workers-startup.timer`.
- **Responsibility:** durable, lease-based, single-host-or-distributed work ledger. Decides
  *what* runs; systemd decides *that* worker processes exist. `QueueWorker` base owns the
  claim → run → succeed/fail loop so workers contain only `handle()` business logic.
- **Inputs:** `enqueue_job()` calls from workers/CLI/HTTP/MCP; job payloads (JSONB, usually
  `{sku: ...}`).
- **Outputs:** job state transitions (audited in `queue_job_history` via trigger);
  `notify()` events on transient requeue (warning) and dead-letter (error).
- **Dependencies:** PostgreSQL ≥ with `pgcrypto`; all worker units have
  `Requires=postgresql.service`.
- **Data stores:** Ledger tables `queue_jobs`, `queue_job_history`, `queue_workers`; also
  `sku_history` (migration) and `todo_items` share the database.
- **External APIs:** none.
- **Failure modes:**
  - PostgreSQL down → every worker blocks/exits; systemd restarts them
    (**ASSUMPTION:** `Restart=` policy in the deployed `/etc/systemd/system/tgw-worker@.service`;
    template is not in the repo).
  - Lease expiry mid-job → `recover_expired_jobs()` requeues; the job's side effects may
    have partially happened — workers must be idempotent (see invariants).
  - Transient-error classification is **substring matching** on error text
    (`_TRANSIENT_ERRORS` in `worker_base.py`) — new error phrasings dead-letter until the
    pattern list is extended.
  - Dead-letter jobs sit silently until an operator acts (`tgw dead-letter`); surfaced via
    `tgw health` per-queue breakdown and notify.
  - Zero-work stalls: a worker can be alive but permanently exhausting its batch on failing
    items (the 2026-06-08 `ebay_sku_migrate` stall) — batch-success verification is a noted
    future pattern, not yet generic.
- **Critical invariants:**
  - Every job handler is **idempotent** — each pipeline worker has a skip condition
    (already-identified, draft present, photos uploaded, price set, listing Active).
  - `dedupe_key` partial-unique index guarantees at most one *active* job per key
    (coalesced rebuilds, self-scheduling singletons).
  - `claim_queue_jobs()` is the only claim path (SKIP LOCKED, priority/run_at order).
  - Dead-letter never auto-retries; re-enqueue requires a fresh dedupe key.
  - Workers never write SQL outside `state_machine.py`.

## 3. tgw-http — HTTP API service

- **Source:** `src/tgw/http_server.py` (FastAPI, uvicorn); unit `tgw-http.service`
  (`etc/systemd/tgw-http.service`); port **7373**.
- **Responsibility:** network surface of the fence for the Flutter app, tablet web forms,
  MC extfs copyin, and the eBay sold-event webhook.
- **Inputs:** authenticated `/api/*` JSON requests (Bearer token from
  `secrets_root/tgw-api-key.json`); unauthenticated `/form/intake/{sku}`, `/form/bulk` HTML
  posts (network-trust); eBay SOAP notifications at `/webhooks/ebay/notification`.
- **Outputs:** `{ok, ...}` JSON; item field writes via the platform layer; enqueued jobs
  (`POST /api/items/{sku}/action`, bulk apply, set-template, PATCH all coalesce a
  `catalog_rebuild`); thumbnail JPEGs.
- **Dependencies:** platform layer; SQLite catalog (list/search source); Ledger (queue
  status, `_queue_jobs` in item detail, enqueue); ItemData (item detail/PATCH).
- **External APIs:** eBay Taxonomy via `GET /api/ebay/aspects/{category_id}` (delegated to
  `apis/ebay/specifics.py`); receives eBay notifications.
- **Failure modes:**
  - Bearer key file missing/unreadable → all `/api/*` requests fail
    (**ASSUMPTION:** 401/500 at request time, not crash at boot).
  - Webhook signature verification incomplete without `dev_id` (ISS-005); current behavior
    accepts unsigned — a forged sold notification could mark an item sold (mitigated:
    listing-id must exist in the 10-min cached index; infra not yet publicly exposed).
  - Webhook always ACKs (`{"ack": "Success"}`) even on internal error, to stop eBay retry
    storms — a processing bug silently drops sold events (recovered later by
    `ebay_legacy_sync` polling).
  - `/form/*` endpoints are writable without auth — safe only while the port is
    LAN/Tailscale-only.
- **Critical invariants:**
  - `sku` immutable in PATCH (400).
  - `location` writes route through `locationupdate()` so the location tree stays in sync.
  - PATCH clears `catalog_verified`; every write path enqueues coalesced rebuild.
  - Only `/webhooks/ebay/notification` is intentionally Bearer-free among API routes.

## 4. token_refresh worker

- **Responsibility:** sole manager of the eBay OAuth user token.
- **Inputs:** self-scheduled jobs (fires when token expires within 30 min); token + app
  credentials from `secrets_root`.
- **Outputs:** refreshed `secrets_root/ebay-token.json`; next self-scheduled job
  (expiry-based).
- **Dependencies:** Ledger; secrets.
- **External APIs:** eBay `POST /identity/v1/oauth2/token`.
- **Failure modes:** refresh token invalidated (HTTP 400 invalid_grant — happened 2026-06-05
  after a scope change) → dead_letter + notify; **every eBay-writing worker then degrades**
  (their jobs requeue on "token is expired" with 900 s backoff). Recovery is manual:
  browser OAuth re-consent (`get_access_token.py`, paste redirect URL at the `→` prompt, or
  `--code` flag) then `tgw restart-ebay-token`.
- **Critical invariants:** the only writer of `ebay-token.json`; worker passes `force=True`
  to bypass the client's internal 5-min guard (the double-buffer bug fix); **scopes are
  locked** — requesting different scopes invalidates the refresh token.

## 5. pm_intake worker

- **Responsibility:** plan automation — turns notes dropped into the vault inbox into
  Master Plan patches.
- **Inputs:** `docs/TGW-Plan-Vault/inbox/*.md` (polled).
- **Outputs:** patched `TGW-Master-Plan.md`; processed notes archived to
  `inbox/processed/`.
- **Dependencies:** Ollama (`qwen2.5:latest`) via `apis/ollama.py` + advisory lock; the
  Syncthing-synced vault.
- **External APIs:** none (local Ollama only).
- **Failure modes:** LLM mis-patches the plan (plan is git-tracked and human-reviewed —
  recovery is git); Syncthing sync conflicts on the vault produce `.sync-conflict-*` files
  (one exists in `.obsidian/` now; a conflict-resolution worker is designed under
  PP-PORTABLE-CATALOG-001, not built); CPU-only inference makes processing slow.
- **Critical invariants:** writes only inside the plan vault — never ItemData or config.

## 6. bundle_intake + multi_intake workers

- **Responsibility:** entry point of the item pipeline. `bundle_intake` turns a photo drop
  into an ItemData record; `multi_intake` splits multi-item bundles into child SKUs.
- **Inputs:** `incoming/newitems/<SKU>/` dirs, `<SKU>.zip`, `newitems/multi/<SKU>/`
  (polled with a 30 s stability gate so half-copied drops aren't consumed).
- **Outputs:** `ItemData/<SKU>/` with media + stub JSON; enqueues `catalog_rebuild` (30 s
  coalesced), `thumbnail_gen`, `ai_identify` per (child) SKU. multi_intake writes
  `source_sku` on children and strips inherited `Item number`.
- **Dependencies:** Ledger; filesystem.
- **Data stores:** ItemData (create); `incoming/` staging (consume).
- **External APIs:** none.
- **Failure modes:** "directory not empty" OS races (classified transient, 30 s requeue);
  duplicate/colliding SKU dirs (**ASSUMPTION:** operator-side SKU generation via camera
  workflow makes collisions effectively impossible — timestamps to the tenth of a second);
  partially-copied media despite the stability gate if a transfer stalls > 30 s then resumes.
- **Critical invariants:** SKU format `tgwYYYYMMDDHHMMSSmmm`; one folder per SKU; intake
  never overwrites an existing `<SKU>.json`; child SKUs must not inherit the parent's
  eBay identity fields.

## 7. ai_identify worker (+ lookup dispatcher)

- **Source:** `workers/ai_identify.py`, `apis/lookup/*` (dispatcher + 8 sources),
  `apis/ollama.py`.
- **Responsibility:** turn photos + hints into a structured identification: title, category,
  description, condition, eBay category.
- **Inputs:** job per SKU; primary photo (resized to 512 px); `ai_hint`, `upc`/`isbn`
  fields; category-group template data (`/opt/TGW/config/category-groups.json`, 25 groups).
- **Outputs:** item fields `title`, `category`, `description`, `condition`,
  `ebay_category_id/name`, `ai_identified: true`, `product_lookup` (on barcode hit),
  `identification_history` append; enqueues `ebay_draft` + `catalog_rebuild`.
- **Dependencies:** Ollama `qwen2.5vl:7b` under the advisory lock; lookup dispatcher with
  30-day cache.
- **External APIs:** upcitemdb (primary), go-upc, open_library, discogs, open_food_facts,
  igdb (Twitch OAuth), justtcg, pricecharting — all optional, keyed from `secrets_root`,
  graceful-skip when absent.
- **Failure modes:** vision model mis-identifies (mitigations: `ai_hint`, templates,
  barcode enrichment via `_USER_PROMPT_ENRICHED`, operator `tgw hint` + re-identify loop,
  full audit trail in `identification_history`); Ollama down/slow → transient requeue;
  lookup API quota/outage → silently proceeds unenriched (`barcode_lookup_fail` is an
  info-level catalog-verify rule, not an error).
- **Critical invariants:** skip if `ai_identified: true` unless `ai_reidentify` set (then
  clears it); every run appends an `identification_history` event; lookup routing is
  strictly additive — a lookup failure must never block identification; single-flight
  Ollama (lock 8472).

## 8. ebay_draft worker

- **Responsibility:** build the complete `draft_listing` block — the offline representation
  of the future eBay listing.
- **Inputs:** identified item JSON; Taxonomy category suggestions + aspects; Browse-API
  aspect hints from similar live listings (`_fetch_browse_aspect_hints`,
  `ASPECT_REFINEMENTS`); condition policy cache (`apis/ebay/conditions.py`, 26 policy sets /
  15 K categories).
- **Outputs:** `draft_listing` (SEO title ≤ 80 chars + `title_ai`/`title_flags`,
  category, condition triple `condition_id/label/enum` via `best_condition()`,
  item_specifics from Ollama (SELECTION_ONLY + FREE_TEXT), description + footer + picklist
  line, quality score, `price: null`); `offline_draft: true` when eBay APIs unreachable;
  enqueues `ebay_upload` + `ebay_price` + `catalog_rebuild`.
- **Dependencies:** Ollama (`qwen2.5`, advisory lock); eBay token.
- **External APIs:** eBay Taxonomy (getCategorySuggestions, getItemAspectsForCategory),
  Browse (aspect hints — fail-soft to `{}`).
- **Failure modes:** offline drafting produces drafts that need taxonomy reconciliation
  later (`offline_draft_stall` catalog-verify rule flags drafts older than 2 h); aspect
  hallucination by the LLM (bounded: SELECTION_ONLY aspects constrained to allowed values;
  recommended/required fill-rates recorded in `aspects_*` counters).
- **Critical invariants:** idempotent — skips if `draft_listing` exists; never blocks on
  Browse enrichment; `draft_listing.title` may differ from top-level `title` (SEO copy) but
  `title_ai` preserves provenance.

## 9. ebay_upload worker

- **Responsibility:** push item photos to eBay Picture Services (EPS) for permanent hosted
  URLs.
- **Inputs:** photos in the SKU folder; `ebay_photos` list (for the skip check).
- **Outputs:** `ebay_photos: [{local, url}]`; `draft_listing.imageUrls`; enqueues
  `catalog_rebuild`.
- **External APIs:** eBay **Trading API** `UploadSiteHostedPictures` (legacy API — the
  Inventory API has no photo upload).
- **Failure modes:** per-photo upload failure leaves a partial `ebay_photos` list (skip
  condition is *all* photos uploaded, so re-runs top up the remainder); token expiry →
  transient requeue.
- **Critical invariants:** EPS URLs are treated as permanent; upload order defines listing
  photo order; downstream `ebay_stage` refuses to stage with zero photos
  ("no ebay photo urls yet" is a recognized 600 s transient).

## 10. ebay_price worker

- **Responsibility:** compute launch price and markdown target from live market comps.
- **Inputs:** `draft_listing` (title/category); category-groups pricing floors; velocity
  stats (`velocity-stats.json`) for the `hold_launch` hint; `category_price_defaults` (cfg).
- **Outputs:** `draft_listing.price` (launch = 110 % of comp max → .99),
  `ebay_offer.{price_comps{count,min,p25,median,p75,max}, target_price=p25, price_source,
  priced_at}`, quality re-score; enqueues `ebay_stage` + `catalog_rebuild`.
- **External APIs:** eBay Browse search (cascade: full title → category+short title →
  category only).
- **Failure modes:** thin comps → Stage-4 fallback to category-group `typical_used ×
  condition_factor`, then hard group floor — items no longer stall on null price but a bad
  group mapping prices the item wrong; Browse results include outliers (110 %-of-max launch
  is deliberately aggressive; the day-3/day-17 markdown schedule is the correction
  mechanism).
- **Critical invariants:** skip if price already set; group **hard floor applies to all
  prices including Browse results**; never prices without recording `price_source` and
  comps provenance.

## 11. ebay_stage worker

- **Responsibility:** create the eBay-side objects *without going live*: inventory item +
  UNPUBLISHED offer. End state of the automated pipeline; everything after is
  operator-gated.
- **Inputs:** fully drafted/priced/photographed item JSON; policy IDs from cfg
  (fulfillment precedence: item `shipping_profile` > `fulfillment_policy_by_category` >
  `fulfillment_policy_by_size_class` > FC4 default; payment + return policy IDs;
  store-category map).
- **Outputs:** `ebay_offer.{offer_id, status=UNPUBLISHED, staged_at}`, `epid` (when
  catalog scope is granted — currently missing); enqueues `catalog_rebuild`. Item becomes
  visible in Seller Hub and `tgw staged`.
- **External APIs:** eBay Inventory API — PUT inventory_item, POST offer.
- **Failure modes:** errorId 25021 (condition rejected) → retry with USED_EXCELLENT;
  25709 prevented by global `Content-Language: en-US`; 25002 Item.Country addressed via
  `availabilityDistributions` + `merchantLocationKey` (ISS-001 — outcome monitored);
  duplicate-offer risk on items with legacy listings — guarded by skipping
  `ebay_listing.status == Active` (but see ISS-008: legacy resolution data is not
  authoritative).
- **Critical invariants:** guards — never stage with null price, no photos, or an Active
  listing; staging must never publish; `weight_oz` flows to `packageWeightAndSize` with a
  0-guard.

## 12. ebay_publish worker (operator-gated)

- **Responsibility:** take a staged offer live. **Manual trigger only** — `tgw publish
  <sku>` after `tgw staged` review; the queue job exists but nothing enqueues it
  automatically.
- **Inputs:** staged item (offer_id, non-null price, photos); `reprice_stages` from cfg.
- **Outputs:** live listing; `ebay_listing.{listing_id, listing_url, offer_id,
  status=Active, api=inventory, published_at}`, `ebay_offer.status=PUBLISHED`,
  `reprice_schedule` (launch day 0 → p75 day 3 → p25 day 17, priced at publish time);
  enqueues `catalog_rebuild`.
- **External APIs:** eBay Inventory `POST offer/{id}/publish`.
- **Failure modes:** category-specific publish rejections (the 25002 family) dead-letter
  for operator diagnosis — correct behavior, money is involved; condition fallback retry on
  25021 mirrors stage.
- **Critical invariants:** **no offer PUT before publish** (PUT is full-replace and strips
  fields — closed issue, must not regress); publishing is the only transition to
  `status=Active`; `reprice_schedule` is frozen into the item at publish (later cfg changes
  affect only future publishes).

## 13. ebay_price_reducer worker

- **Responsibility:** scheduled markdown — walk published items' `reprice_schedule` and
  apply due price drops.
- **Inputs:** self-scheduled every 6 h; items with `reprice_schedule`; `reprice_skip` flag.
- **Outputs:** updated offer price on eBay; `reprice_schedule[i].done_at`;
  `draft_listing.price` / `ebay_offer.price` updates.
- **External APIs:** eBay Inventory offer PUT (full-replace — must send complete body).
- **Failure modes:** offer PUT full-replace semantics: an incomplete body silently strips
  listing fields (same class of bug as the closed publish issue — the invariant lives here
  too); a stage applied late (worker down) applies on next run — schedule is
  date-due-based, not cron-exact.
- **Critical invariants:** never raises price; honors `reprice_skip`; marks `done_at`
  exactly once per stage; single-flight via dedupe self-schedule.

## 14. ebay_sync + ebay_legacy_sync workers

- **Responsibility:** mirror eBay-side truth back into item JSON. `ebay_sync` covers
  Inventory-API offers/listings (every 6 h); `ebay_legacy_sync` covers Trading-API
  listings and **sold detection** via GetOrders (90-day windows, 365-day initial lookback,
  cursor in `runtime/state/ebay-sold-sync-state.json`).
- **Inputs:** self-scheduled jobs; eBay listing/offer/order state.
- **Outputs:** `ebay_listing.{status, listing_status, live_price, synced_at}`,
  `ebay_offer.{status, category_id, quantity}`; on sale: `status=sold` + `ebay_sale` block
  (`_mark_item_sold()`, shared with the webhook path); enqueues `catalog_rebuild` only for
  changed items.
- **External APIs:** eBay Inventory (offers), Trading (GetMyeBaySelling, GetOrders).
- **Failure modes:** sold-match by listing_id fails for unmigrated/archived legacy items
  (the archive-tombstone gap: ~22 K archive entries permanently outside the 2-year CSV
  ceiling — accepted); sync lag up to 6 h means a sold item can stay listed locally
  (webhook path exists to close this but infra is not deployed); state-file corruption
  resets the lookback window (**ASSUMPTION:** worst case is re-scanning old orders, which
  `_mark_item_sold` idempotency absorbs).
- **Critical invariants:** sync writes only `ebay_*`/status mirror fields — never touches
  draft content; `_mark_item_sold()` is idempotent; rebuild enqueued per *changed* item
  only.

## 15. ebay_sku_migrate worker

- **Responsibility:** long-running migration of legacy live listings to the new SKU scheme:
  delist (EndItem) → rename SKU → relist (AddFixedPriceItem). ~8,350 remaining at
  ~10/hour (config), ETA measured in months.
- **Inputs:** hourly self-schedule; `ebay_sku_migrate` cfg block (`enabled`, `batch_size`,
  `interval_hours`) — read via `cfg['raw']` (ISS-004).
- **Outputs:** renamed ItemData folders/SKUs, `source_sku` field; `sku_history` PostgreSQL
  table; rollback manifests at `var/log/sku-migrate-*.json`.
- **External APIs:** eBay Trading (EndItem, AddFixedPriceItem).
- **Failure modes:** the 2026-06-08 stall: batch capacity exhausted on 5 permanently-failing
  items (Best Offer policy restriction) with **zero visible errors** — fixed, but the
  pattern (silent zero-work loop) is a known platform gap; delist-then-relist is a
  non-atomic two-step against a live marketplace: a crash between steps leaves an item
  delisted (rollback manifest exists for recovery; relist also resets listing age/sales
  history).
- **Critical invariants:** rate-limited and pausable (`enabled: false`); every migration
  recorded in `sku_history` + manifest before destructive steps; carries **its own
  fulfillment-policy copy** — flagged for parity with the main resolver once migration
  completes (deliberate freeze while it runs).

## 16. catalog_rebuild + thumbnail_gen workers (catalog subsystem)

- **Responsibility:** rebuild every derived read model from ItemData: `tgwcatalog.db`
  (SQLite), `search-catalog.json`, CSVs, `by-location/` symlink tree; thumbnail_gen
  generates the single `{sku}.jpg` fast-path after intake; full sweeps via
  `tgw build-thumbnails`.
- **Inputs:** coalesced jobs (dedupe `catalog_rebuild:pending`, 30 s `not_before`);
  per-SKU thumbnail jobs; all of ItemData.
- **Outputs:** the derived stores listed above; `velocity-stats.json` is *not* here (see
  velocity_stats).
- **Dependencies:** Pillow (thumbnails); filesystem.
- **Failure modes:** full rebuild is O(55 K items) per run — write bursts coalesce but a
  steady trickle of writes keeps it cycling (**ASSUMPTION:** rebuild duration is minutes;
  acceptable today, the satellite/dirty-flag design in the master plan is the eventual
  fix); a rebuild crash leaves catalogs stale, not corrupt (next run overwrites);
  "directory not empty" race classified transient.
- **Critical invariants:** derived stores are always reproducible from ItemData — **no
  data may live only in a catalog**; one pending rebuild at a time (dedupe); thumbnails
  share an identical path layout on master and satellites.

## 17. velocity_stats worker

- **Responsibility:** nightly aggregation of sold-item velocity per eBay category
  (1,540 categories) feeding pricing (`hold_launch` hint, category-group reseed) and
  `tgw velocity-report`.
- **Inputs:** nightly self-schedule; ItemData `ebay_sale` blocks (~3,083 sold records).
- **Outputs:** `catalog_root/velocity-stats.json`.
- **External APIs:** none.
- **Failure modes:** sold data is incomplete by construction (2-year CSV ceiling + archive
  gap) so velocity skews recent — acceptable and known.
- **Critical invariants:** read-only over ItemData; output is a derived, regenerable file.

## 18. Token-free utility workers: echo, itemdata_scrub, photo_history_recovery

- `echo` — pipeline liveness probe and the reference worker implementation (also in
  `reference/echo.py`).
- `itemdata_scrub`, `photo_history_recovery` (`workers/`, with counterparts in `tools/`) —
  batch data-quality passes (e.g. `#VERIFIED`→`verified` rename over 55,226 items).
  **Not in `WORKER_QUEUES`** — run ad hoc, not as standing systemd units.
- **Invariant:** scrub passes go through the fence like everything else and enqueue a
  rebuild when they mutate items.

## 19. MCP server (`tgw-mcp-server`)

- **Source:** `src/tgw/mcp_server.py` (FastMCP); console script `tgw-mcp-server`.
- **Responsibility:** expose platform operations to AI agents (Claude Code) as 10 tools:
  `tgw_get_item`, `tgw_search_items`, `tgw_queue_status`, `tgw_health`, `tgw_enqueue`,
  `tgw_get_todo`, `tgw_add_suggest`, `tgw_hint_trail`, `tgw_catalog_verify`,
  `tgw_dead_letter`.
- **Inputs/outputs:** MCP tool calls ↔ same `{ok, ...}` payloads as the CLI.
- **Dependencies:** platform layer, Ledger, SQLite catalog; registration is
  operator-controlled in Claude settings (cannot self-register).
- **Failure modes:** stdio-spawned per session (**ASSUMPTION** — standard FastMCP stdio
  transport); a crash affects only the agent session, never the pipeline.
- **Critical invariants:** strictly a thin adapter over the fence — no separate write
  paths; `tgw_health` runs with `include_ebay=False` (no live eBay calls from agent
  context).

## 20. Notify subsystem

- **Source:** `src/tgw/notify.py`; cfg block `notifications` (live: backends `log,file`,
  min_level info, file `/opt/TGW/var/log/notifications.jsonl`).
- **Responsibility:** uniform operator alerting: worker transient requeues (warning),
  dead-letters (error), available backends log / file / desktop / webhook / smtp (smtp
  fail-soft, off by default).
- **Failure modes / invariants:** `configure()` is wrapped at worker startup so a notify
  misconfiguration **can never prevent a worker from starting**; backends are fire-and-
  forget — notification loss is acceptable, notification blocking is not.

## 21. Backup service (`trader-grims-backup.service`)

- **Responsibility:** continuous inotify-watch + rsync hardlink snapshots of the data tree.
- **Inputs:** filesystem events. **Outputs:** hardlink snapshot tree
  (**ASSUMPTION:** target path defined in root-owned `/opt/TGW/config/trader-grims-backup.yaml`;
  not readable from the repo).
- **Failure modes:** same-host backups — no off-machine copy in the automated path
  (Google Drive DR is planned under PP-NIXOS-001/PP-BACKUP-001); root-owned config `0644`
  flagged by the permissions audit; PostgreSQL is **not** covered by file snapshots alone
  (live WAL — consistent dumps need `pg_dump`, which the PP-DEPLOY-001 runbook includes
  pre-snapshot but nothing schedules routinely — **ASSUMPTION** based on absence of any
  pg_dump timer in the repo).
- **Critical invariants:** read-only over the data tree; PP-BACKUP-001 will split this into
  its own repo and replace it with a unified DR suite — treat current service as frozen.

## 22. Client applications

- **Flutter app** (`apps/`, package `tgw_app`; `flutter/` is a vendored Flutter SDK
  checkout): browse/search (SQLite-backed list endpoints), item detail + edit (GET/PATCH),
  SKU lookup, offer-form aspects via `/api/ebay/aspects/{id}`. Bearer-token client of
  tgw-http only — **no direct data access**.
- **Web forms** (`/form/intake/{sku}`, `/form/bulk`): tablet-first, unauthenticated-by-
  design HTML for warehouse-floor use (template chips, weight, barcode, condition, bulk
  preview/apply).
- **MC integration** (`etc/interfaces/mc/`): `tgwitem` extfs (item fields/ebay/pipeline/
  actions as a virtual filesystem; copyin = field writes through the API) and `tgwlogs`
  (read-only journalctl, queue-allowlisted, argv-list subprocess).
- **Shell layer** (`etc/interfaces/shell/tgw.source`, sha256-pinned): thin one-line
  wrappers over `tgw` CLI; remaining direct-jq writers are deprecated-for-removal.
- **Failure modes:** form/app writes race operator CLI edits (last-writer-wins per field
  batch — see §1); stale catalog reads between rebuilds.
- **Critical invariants:** every client goes through tgw-http or the CLI — the fence holds
  for all of them.

## 23. Plan vault + AI delegation (planning plane)

- **Components:** Obsidian vault `docs/TGW-Plan-Vault/` (Syncthing-synced), master plan,
  `inbox/` (→ pm_intake), `SUGGESTIONS.md` (`tgw suggest`), reference library,
  `perplexity/` briefs, PostgreSQL `todo_items` (`tgw todo` — **canonical task queue**),
  delegation tracks (Claude build / Gemini data / Perplexity research / operator).
- **Failure modes:** Syncthing conflicts (visible artifacts exist; resolution worker
  designed, not built); plan/code drift (multiple stale-done corrections in history —
  mitigated by the todo-tracker-is-canonical rule and periodic code-verified audits).
- **Critical invariants:** plan is reference spec, todo tracker is execution truth; inbox
  notes are processed-then-archived, never edited in place.

---

## Cross-cutting invariants (apply to everything)

1. ItemData JSON is the only canonical item state; everything derived must be regenerable.
2. All ItemData access goes through the fence (`tgw` library / HTTP / MCP — same code).
3. Every mutation enqueues `catalog_rebuild`; rebuild is never inline.
4. All jobs are idempotent and carry skip conditions; dead_letter requires a human.
5. One JSON object with `ok` per CLI/API call.
6. Secrets only in `secrets_root` (700/600, user `tgw`); never world-readable; eBay scopes
   locked.
7. Ollama inference is globally single-flight (advisory lock 8472).
8. Publishing to eBay is operator-gated; no automated path reaches `status=Active`.
9. Commits are operator-gated (Dave controls git history).
10. Workers need a systemd restart to pick up source changes — deploy is not complete until
    affected units restart.
