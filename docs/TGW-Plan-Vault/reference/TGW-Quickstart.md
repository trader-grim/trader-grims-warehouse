---
title: TGW Operator Quickstart
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 2
updated: 2026-06-08
---

# TGW Operator Quickstart

Single-page command reference, organised **by workflow** (not alphabetically), so the
operator never has to dig through the master plan for command syntax. Every `tgw`
subcommand below is cross-checked against `src/tgw/api.py`. Terse by design:
**command → one-line purpose → key flags**.

Conventions:
- Run everything as the `tgw` user (source files are `rw-------`, secrets `chmod 600`).
  Prefix with `sudo -u tgw` from another account, or `runuser -u tgw -- ...`.
- Most write commands accept `--check-only` (validate, no write) or `--dry-run`
  (preview, no side effects). Reach for these first.
- Every `tgw` command prints a JSON result with an `ok` key.
- `--config PATH` overrides the default config on any subcommand.

---

## 1 — System health & status

- `tgw health` — run all platform health checks. Flags: `--no-ollama`, `--no-ebay`.
- `tgw status` — alias for `tgw health` (same flags).
- `tgw restart-workers [queues...]` — restart `tgw-worker@<queue>.service` units (sudo if
  not root). No args = all canonical workers. `--dry-run` prints the systemctl command only.
- `tgw dead-letter` — inspect/manage the dead_letter queue. `--queue Q`, `--limit N`,
  `--requeue JOB_ID` (re-enqueue one job), `--requeue-transient` (batch re-enqueue every
  `[transient]`-classified job, honours `--queue`), `--cancel QUEUE` (cancel all in a queue).
- `tgw restart-ebay-token` — clear dead-letter token jobs and enqueue a fresh
  `token_refresh` immediately.

### Raw systemd / psql idioms

- List worker units: `systemctl list-units 'tgw-worker@*'`
- Follow one worker's log: `journalctl -u 'tgw-worker@<queue>.service' -f`
- Restart one worker after a source change:
  `systemctl restart tgw-worker@<queue>.service`
- Queue state snapshot:
  ```bash
  psql -U tgw state_machine -c "
    SELECT queue_name, state, count(*) FROM queue_jobs
    GROUP BY queue_name, state ORDER BY queue_name, state;"
  ```
- Re-enqueue after `dead_letter` (no auto-retry): `state_machine.enqueue_job()` with a
  fresh dedupe key, or `tgw dead-letter --requeue JOB_ID`.

---

## 2 — Item intake workflow

The pipeline flow (each stage enqueues the next):
**photo intake → `ai_identify` → `ebay_draft` → `ebay_price` → `ebay_stage` →
`tgw staged` operator review → `tgw publish` → live.**

### Start an item

- `tgw set-template [GROUP_KEY] [SKU]` — apply category-group defaults to an item
  (PP-INTAKE-001). `--list` lists template groups; `--camera GROUP_KEY` pushes a
  `SETTEMPLATE:` marker to clipboard only (KDE Connect relay, no JSON write);
  `--dry-run`. Omit SKU to use the `CurrentItem` symlink.
- **Web form** `/form/intake/{sku}` — mobile/tablet intake form served by `tgw serve`
  (FastAPI on `127.0.0.1:7373`). Fill fields → submit → updates item JSON.
- `tgw serve` — start the tgw-http FastAPI service. `--host`, `--port` (default 7373),
  `--reload` (dev only).

### Drive / re-drive the pipeline

- `tgw enqueue-sku QUEUE SKU...` — enqueue pipeline action(s). QUEUE is always first
  (queue-first path), then one or more SKUs. Pass `-` as the SKU argument to read one SKU
  per line from stdin. Queues: `ai_identify`, `ebay_draft`, `ebay_price`, `ebay_stage`,
  `ebay_publish`, `alt_text`, ...

  **Pipe patterns** — `--skus-only` on `list`/`search` emits one SKU per line, pipe-safe:
  ```bash
  # Re-identify everything at a location
  tgw list --location BIN-A1 --skus-only | tgw enqueue-sku ai_identify -

  # Re-draft all staged items (e.g. after a template change)
  tgw list --status staged --skus-only | tgw enqueue-sku ebay_draft -

  # Multi-SKU inline (no pipe needed for a handful)
  tgw enqueue-sku ebay_price tgw20240101120000001 tgw20240101120000002

  # Search + re-queue (e.g. all "headphones" items needing a new draft)
  tgw search "headphones" --status ai_identified --skus-only | tgw enqueue-sku ebay_draft -
  ```

- `tgw hint SKU TEXT...` — set an `ai_hint` and re-queue identification. `--force`
  re-identifies even if already `ai_identified`.
- `tgw hint-trail SKU` — show the identification history for an item.
- `tgw lookup SKU` — run product-enrichment lookup for one item (PP-LOOKUP-001).
  `--force` ignores cache; `--save` writes result back to item JSON.
- `tgw resolve-legacy SKU...` — mark item(s) as legacy-eBay-cleared so `ebay_stage` can
  proceed. `--no-stage` marks resolved without enqueuing `ebay_stage`.

### Inspect / edit one item

- `tgw get SKU` — full item record by SKU.
- `tgw search TEXT` — text search (shorthand for `list --search TEXT`). `--location`,
  `--status`, `--limit N` (default 20), `--skus-only`.
- `tgw update SKU FIELD VALUE` — update one field on one item. `--check-only`.
- `tgw update-title SKU VALUE` — update the title field. `--check-only`.
- `tgw update-location SKU LOCATION` — update location + rebuild tree link. `--check-only`.
- `tgw update-verified SKU VALUE` — update VERIFIED (writes `verified` + `#STATUS=In Stock`).
- `tgw update-status VALUE SKU...` — set `#STATUS` on one or more items. Note: VALUE
  comes first so multiple SKUs can follow. `--check-only`.
- `tgw set-shipping SKU VALUE` — per-item `shipping_profile` override (PP-HINT-001); VALUE
  is a profile name or raw fulfillment policy id. `--check-only`.
- `tgw quality SKU...` — listing-quality score for one or more items (PP-QUALITY-001).
  `--save` writes the score back to `draft_listing`.
- `tgw queue-history SKU` — show pipeline state-transition history for an item.
  `--queue Q`, `--job-id UUID`, `--limit N`, `--json`.

> **Deprecated aliases** (still work; use hyphenated forms above): `titleupdate`,
> `locationupdate`, `verifiedupdate`, `statusupdate`, `setshipping`, `whispertosuggest`.

---

## 3 — Bulk operations

- `tgw bulk --field F --value V [skus...]` — bulk-edit one field across matched items
  (PP-BULKEDIT-001). Field ∈ `title|location|status|ai_hint|shipping_profile`. Filters:
  `--location`, `--status`, `--search`, `--limit N`. **Dry-run unless `--apply`.**
- **Web form** `/form/bulk` — tablet-first bulk editor (filter → preview → apply),
  served by `tgw serve`.
- `tgw mvitems TO_LOCATION [skus...]` — move items to a location (replaces `catlocmvall`;
  PP-SHELL-001). Selectors: `--from LOCATION`, `--search`, `--status`. `--check-only`.
- `tgw update-where FIELD VALUE` — bulk-update matched items. Selectors: `--location`,
  `--status`, `--date-from`, `--date-to`, `--search`. `--check-only`.
- `tgw requeue` — bulk-enqueue `ai_identify` for items matching a filter. **Dry-run unless
  `--run`.** Filters: `--no-title`, `--unidentified`, `--hint-set`, `--no-draft`,
  `--no-price`, `--catalog-only`; `--limit N` (default 100, 0 = unlimited).
- `tgw catalog-verify` — scan ItemData for assumption violations → checklist (PP-VERIFY-001).
  `--location`, `--limit N`, `--severity critical|warning|info`, `--output PATH`, `--json`,
  `--mark-verified` (+`--force`), `--skip-verified`, `--fix` (+`--write` to apply).
- `tgw catlocmvall FROM TO` — **deprecated**; use `tgw mvitems`.

### Catalog builds (always run as jobs in production; CLI is for manual rebuilds)

- `tgw build-all` — full catalog + search catalog + location tree + SQLite. `--check-only`.
- `tgw build-full` — full catalog JSON. `tgw build-full-csv` — full catalog CSV.
- `tgw build-search` — search catalog JSON (`--source auto|full_catalog|itemdata`).
  `tgw build-search-csv` — search catalog CSV.
- `tgw build-locations` — location symlink tree (`--source auto|search_catalog|...`).
- `tgw build-sqlite` — build `tgwcatalog.db` SQLite catalog.
- `tgw build-thumbnails` — per-SKU thumbnail cache (needs Pillow).
- `tgw build-fingerprints` — build the visual fingerprint index (`fingerprints.db`) over the
  thumbnail cache (PP-VISION-001). `--limit N` (partial build), `--check-only`.
- `tgw ensure-catalog` — build search catalog only if missing.

### Visual search & portable catalog

- `tgw locate IMAGE` — rank catalog SKUs by visual similarity to a query image
  (dHash + colour histogram; PP-VISION-001). `--size-class CLASS` (restrict to a size class —
  no-op until `size_class` is populated on items), `--top N` (default 10), `--json`.
  Requires `tgw build-fingerprints` first.
- `tgw export-catalog DEST` — copy `tgwcatalog.db` + thumbnails to a directory for Syncthing
  relay to a tablet / spare client (PP-PORTABLE-CATALOG-001 Phase 1). `--no-thumbnails`,
  `--limit N` (cap thumbnails), `--check-only`.

---

## 4 — eBay management

- `tgw ebay-pull` — on-demand eBay data pull: active listings + sold orders → ItemData.
  `--no-active`, `--no-sold`, `--dry-run`.
  (Note: continuous sync runs in the `ebay_sync` worker; there is no `tgw ebay-sync` CLI.)
- `tgw ebay-sweep` — physical-inventory checklist for ambiguous-status items.
  `--groups A,B,C` (A=active/unclear, B=out-of-stock/no-listing, C=no-status/no-listing),
  `--location`, `--limit N`, `--output FILE`.
- `tgw reprice-suggest [skus...]` — read-only price suggestions from market data
  (PP-REPRICER-001); never writes to eBay. Filters: `--location`, `--status`, `--search`,
  `--limit N`; `--json`.
- `tgw staged` — list items staged as UNPUBLISHED eBay offers, awaiting review. `--json`.
- `tgw publish SKU...` — approve and publish one or more staged items. `--dry-run`.
- `tgw import-sold-csv FILE` — import eBay Seller Hub sold-orders CSV → mark items sold.
  `--dry-run`, `--show-columns`, `--fuzzy` (title-similarity pass),
  `--fuzzy-threshold N` (default 0.80).
- `tgw restart-ebay-token` — clear dead-letter token jobs + enqueue fresh `token_refresh`.
- `tgw get-ebay-token` — browser OAuth re-consent flow (use when refresh token is dead /
  HTTP 400). `--sandbox`; `--code` to paste an auth code directly (skips browser).
- `tgw setup-ebay-hooks --url URL` — register eBay push-notification delivery URL (run once).
  `--check` prints the currently registered URL without changing it.
- `tgw seo-audit` — SEO quality report for live + staged listings (PP-SEO-001).
  `--limit N` (default 50, worst first), `--live-only`.
- `tgw store-categories` — list eBay store custom categories via GetStore (PP-STORE-001).
- `tgw strikethrough-check` — show strikethrough-pricing config + MSRP coverage (PP-STRIKE-001).
- `tgw velocity-report` — sold-velocity analytics by eBay category (PP-PRICE-004).
  `--category CAT_ID`, `--refresh`, `--json`, `--output FILE`, `--min-sold N`.
- `tgw category-groups [CAT_ID]` — view/manage category-group taxonomy (PP-PRICE-005).
  `--list`, `--reseed` (re-seed `pricing.typical_used` from velocity stats).

### eBay auth notes

- OAuth user token is refreshed by the `token_refresh` worker, stored in `secrets_root`.
- All Inventory API PUT/POST include `Content-Language: en-US` (set centrally in `client.py`).
- Approved scopes are **locked** — do not add scopes speculatively (it has broken OAuth).
- Paste the redirect URL at the Python `→` input() prompt, not in bash.

---

## 5 — Admin & diagnostics

- `tgw todo [agent]` — multi-agent TODO tracker (PP-TODO-001); this is the **canonical task
  queue** (not the plan tables). Agent ∈ `claude|admin|gemini|db` (omit = all).
  CRUD flags: `--add TEXT` (+`--priority N`, `--source SRC`), `--done ID`, `--all`,
  `--update ID TEXT...`, `--delegate ID AGENT`, `--set-priority ID N`, `--seed`.
- `tgw suggest TEXT...` — append a suggestion for the next planning session.
  Aliases: `tgw note TEXT...`, `tgw btw TEXT...`.
- `tgw suggest-edit` — open `SUGGESTIONS.md` in `$EDITOR` before PM-intake. `--pending-only`
  extracts only unprocessed `[ ]` entries.
- `tgw quiet-check` — when the pipeline is idle, surface pending suggestions/TODOs
  (PP-CAPTURE-001). `--notify` also fires a desktop/webhook notification.
- `tgw claude-help [issue]` — launch a Claude troubleshooting session with TGW context
  (PP-CLAUDE-HELP-001). `--worker NAME`, `--launch` (exec now vs. print the command).
- `tgw clip ACTION [pattern]` — clipboard history store/query (PP-CLIP-001). ACTION ∈
  `list|last-sku|search|wipe`. `--limit N`, `--sku-only`.
- `tgw dead-letter` — inspect/manage dead_letter jobs (see §1 for flags).
- `tgw health` / `tgw restart-workers` — see §1.

### Maintenance / data hygiene

- `tgw data-scrub` — ItemData maintenance passes. `--pass N` (default 1 =
  `#VERIFIED→verified` rename), `--write` to apply (dry-run by default).
- `tgw sku-migrate` — SKU normalization (PP-ADD-005). `--check-collisions`, `--class A,B,...`,
  `--include-live-ebay`, `--limit N`, `--manifest PATH`. **Dry-run by default; `--run` to apply.**
- `tgw build-archive-index` — scan ItemArchive zips → eBay-ID lookup cache (run once).
  `--archive-dir`, `--cache`.
- `tgw resolve [selectors]` — resolve identifiers to a set of SKUs. `--sku`, `--location`,
  `--status`, `--date-from/--date-to`, `--ebay-item-id`, `--upc`, `--search`.
- `tgw list` — list items with optional filters. `--search`, `--location`, `--status`,
  `--date-from/--date-to`, `--limit`.
- `tgw picklist` — location-sorted picking list (PP-FULFILLMENT-001). `--status`,
  `--location`, `--search`.

### Capture / research helpers

- `tgw perp-run [brief_id]` — load a Perplexity research-brief prompt to clipboard
  (PP-PERP-AUTO-001). `--list` to list briefs.
- `tgw whisper-suggest WAVFILE` — transcribe a WAV via whisper-cli and file it as a
  suggestion (PP-WHISPER-001). `--model PATH`. (Alias: `whispertosuggest`.)

---

## 6 — MC console interface (Midnight Commander)

TGW exposes its data as Midnight Commander **extfs VFS** plugins — browse the warehouse
inside MC as if it were a filesystem. Deploy with
`sudo bash etc/interfaces/mc/install-system-mc.sh`.

### extfs VFS list

- `tgwitem` — browse one SKU's JSON as a VFS: `meta.json`, `fields/` (one `.txt` per field,
  editable via `copyin`), `photos/`, `ebay/` (read-only `draft`/`offer`/`listing`/`reprice`/
  `lookup`), `pipeline/` (live PostgreSQL job state per queue), `actions/`
  (`re-identify`/`re-draft`/`re-price`/`re-stage`/`re-publish` — press Enter to enqueue).
- `tgwcatalog` — 55K+ items organised by location as a navigable VFS (reads `tgwcatalog.db`,
  falls back to `search-catalog.json`).
- `tgwqueue` — live PostgreSQL queue snapshot; subdirs per state, one file per job.
- `tgwhealth` — platform health checks as named `OK_` / `FAIL_` files.
- `tgwservices` — systemd TGW service status (all `tgw-worker@*` + `tgw-http`).
- `tgwlogs` — read-only recent `journalctl` output per worker (PP-MC-001 Phase 4).

### F2 menu keys (top-level MC)

`v` = VFS guide · `h` = health · `q` = queue · `s` = services · `l` = catalog stats ·
`i` = item summary · `p` = image preview.

---

## 7 — Qtile + macroboard quick-reference

### Qtile — Super+T chord mode (bar shows `[ TGW ]`)

Press **Super+T** to enter TGW command layer, then a single key:

`h` = health · `q` = queue depths · `s` = staged · `t` = todo · `v` = velocity-report ·
`c` = clipboard SKU action (last-sku) · `o` = open ItemData in Dolphin ·
`1`–`2` = pipeline triggers · `F2`/`F4` = workspace jump · **Escape** = exit mode.

Other Qtile bindings:
- **F12** — toggle the scratchpad terminal (floating konsole, always-available TGW shell).
- Named workspaces: `shell` / `tgw` / `ebay` / `agents` / `media`.
- Install (as desktop user): `bash etc/interfaces/qtile/install.sh`.

### keyd macroboard — TGW layer (Caps Lock to enter; ESC or Caps Lock to exit)

Highlight a SKU/identifier anywhere on screen, then press a key. With nothing selected,
macros fall back to `CurrentItem`. `S-` = Shift held.

**Item info & fields**
- `g` Get summary → notify · `t` Title update (prompt) · `l` Location update (prompt) ·
  `v` Verified (mark In Stock) · `h` Hint → requeue identify · `u` set cUrrent item

**Pipeline (in order)**
- `1` ai_identify · `2` ebay_draft · `3` ebay_price · `4` ebay_stage · `5` publish ·
  `p` Publish (same as 5)

**Picklist**
- `a` Add picklist line · `S-a` Add question line

**Location bulk**
- `m` Move all in location · `S-l` Open location folder

**Open / view**
- `o` Open folder (Dolphin) · `i` Images (gwenview) · `j` JSON edit (konsole)

**eBay browser**
- `e` eBay search by SKU · `b` Browse listing (ebay.com/itm) · `S-e` Edit/revise listing ·
  `f` Find sold comparables · `S-s` Seller Hub overview

**Admin / system**
- `k` health checK → notify · `q` Queue depths → notify · `c` Catalog rebuild ·
  `d` stageD items list · `w` Weight (USB scale → clipboard) · `y` whisper dictation (15s) ·
  `z` short dictation (7s) · `x` suggest (plan inbox) · `r` Requeue --no-draft count

Navigation keys (Enter, arrows, F-keys, Backspace) pass through normally. Install is
operator-gated on a dedicated second keyboard (see PP-MACRO-001 in the master plan).

---

## 8 — Worker reference table

All 18 canonical worker queues (`WORKER_QUEUES` in `src/tgw/queue/__init__.py`). Restart any
one with `systemctl restart tgw-worker@<queue>.service`, or several / all with
`tgw restart-workers [queue...]`. Tail with `journalctl -u 'tgw-worker@<queue>.service' -f`.

| Queue | Purpose | Restart command |
|-------|---------|-----------------|
| `token_refresh` | Refresh the eBay OAuth user token | `systemctl restart tgw-worker@token_refresh.service` |
| `pm_intake` | PM-intake: fold inbox notes into the master plan | `systemctl restart tgw-worker@pm_intake.service` |
| `bundle_intake` | Process bundled-photo intake into per-SKU items | `systemctl restart tgw-worker@bundle_intake.service` |
| `multi_intake` | Multi-item intake splitting | `systemctl restart tgw-worker@multi_intake.service` |
| `ai_identify` | AI vision identify (Ollama) + barcode product lookup | `systemctl restart tgw-worker@ai_identify.service` |
| `catalog_rebuild` | Rebuild catalogs (full/search/locations/SQLite) | `systemctl restart tgw-worker@catalog_rebuild.service` |
| `thumbnail_gen` | Generate per-SKU thumbnail cache | `systemctl restart tgw-worker@thumbnail_gen.service` |
| `ebay_draft` | Build the eBay draft listing (title/desc/aspects) | `systemctl restart tgw-worker@ebay_draft.service` |
| `ebay_upload` | Upload inventory item + media to eBay | `systemctl restart tgw-worker@ebay_upload.service` |
| `ebay_price` | Set launch price (110% max → .99) | `systemctl restart tgw-worker@ebay_price.service` |
| `ebay_price_reducer` | Scheduled markdown (p75 day 3 → p25 day 17) | `systemctl restart tgw-worker@ebay_price_reducer.service` |
| `ebay_stage` | Create UNPUBLISHED offer (stage for review) | `systemctl restart tgw-worker@ebay_stage.service` |
| `ebay_publish` | Publish an approved staged offer → live | `systemctl restart tgw-worker@ebay_publish.service` |
| `ebay_sync` | Continuous active-listing + sold-order sync | `systemctl restart tgw-worker@ebay_sync.service` |
| `ebay_legacy_sync` | Reconcile pre-platform legacy eBay listings | `systemctl restart tgw-worker@ebay_legacy_sync.service` |
| `ebay_sku_migrate` | Migrate live eBay listings to TGW SKU format | `systemctl restart tgw-worker@ebay_sku_migrate.service` |
| `velocity_stats` | Nightly sold-velocity stats by category | `systemctl restart tgw-worker@velocity_stats.service` |
| `echo` | Diagnostic echo worker (queue plumbing test) | `systemctl restart tgw-worker@echo.service` |

### Worker console scripts (`pyproject.toml [project.scripts]`)

Each worker has an installed entry point (run by the systemd template via
`tgw-queue-launcher`): `tgw-token-worker`, `tgw-pm-intake-worker`, `tgw-bundle-intake-worker`,
`tgw-multi-intake-worker`, `tgw-ai-identify-worker`, `tgw-catalog-rebuild-worker`,
`tgw-thumbnail-gen-worker`, `tgw-ebay-draft-worker`, `tgw-ebay-upload-worker`,
`tgw-ebay-price-worker`, `tgw-ebay-price-reducer-worker`, `tgw-ebay-stage-worker`,
`tgw-ebay-publish-worker`, `tgw-ebay-sync-worker`, `tgw-ebay-legacy-sync-worker`,
`tgw-ebay-sku-migrate-worker`, `tgw-velocity-stats-worker`, `tgw-echo-worker`.
Other entry points: `tgw` (CLI), `tgw-mcp-server` (MCP server), `tgw-browser`,
`tgw-queue-launcher`.

---

## 9 — Physical processes (STUB)

> **TODO: Dave to fill in.** These are the hands-on, off-keyboard procedures that the
> software reference above cannot capture. Document the actual physical steps so a new
> operator (or future Dave) can run the station unaided.

### 9.1 Intake station setup — TODO

- TODO: bench layout, lighting, what plugs in where.
- TODO: which workstation/account the intake form runs from; how to reach `/form/intake`.
- TODO: SKU-label / numbering convention at the physical bench.

### 9.2 USB scale — TODO

- TODO: scale model + connection (which USB port / dock).
- TODO: how a weight reaches the item (macroboard `w` key → clipboard → which field).
- TODO: units, zeroing/taring procedure, troubleshooting a non-reading scale.

### 9.3 Camera / Foldio360 trigger — TODO

- TODO: camera model + tether/Wi-Fi setup; Foldio360 turntable wiring.
- TODO: capture trigger (button / foot pedal / software) and how photos land in the
  item folder.
- TODO: photo count + angles convention per item; background/lighting settings.

### 9.4 Label printing — TODO

- TODO: label printer model + driver/queue name.
- TODO: what gets printed (shipping label vs. shelf/location label vs. SKU tag).
- TODO: print trigger from the pipeline, and how to reprint a damaged label.
