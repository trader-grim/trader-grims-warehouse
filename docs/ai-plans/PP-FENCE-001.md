# PP-FENCE-001: ItemData write fence — workers read-only, all writes through tgw-api

**Status:** Draft — 2026-06-28
**PP ref:** PP-FENCE-001

---

## Problem / motivation

The settled architecture states: *"tgw-api is the fence — all ItemData reads/writes go
through it."* In practice every worker calls `atomic_write_json` directly. There are
**~30 call sites** across 11 worker files and 2 ebay/ modules. Consequences:

- No single write path → no consistent before/after capture, no audit trail, no replay.
- Workers conflict with Seller Hub edits they cannot detect because nothing coordinates
  concurrent writes.
- Data loss is systemic: each worker independently decides what to record, and most
  discard the raw LLM output, eBay API responses, and price change events.
- Invariant A4 in `reference/invariants.md` is marked ⚠️ partial for exactly this reason.

The fix is structural: make it *physically impossible* for workers to write ItemData by
running them as an OS user that has read-only filesystem access, then building the fence
endpoints they must use instead.

---

## Constraints (from settled architecture)

- **tgw-api is the fence** — all ItemData reads/writes go through it. This PP enacts
  that invariant at the OS permission level, not just by convention.
- **Workers are thin** — they ask tgw-api, never construct paths directly.
- **Output contract** — every API call returns `{ok, ...}`.
- **Secrets from `secrets_root`** — workers may still read secrets; the fence is for
  ItemData only.
- **Catalog rebuild is always a job** — the fence write path enqueues catalog_rebuild
  after every item mutation; workers must not call `build_all_catalogs()` inline.
- **NixOS owns users/groups** — all OS-level changes go to `~/tgw-flake`, not the
  Python source repo.

---

## Proposed approach

### Layer 1 — OS permissions (NixOS)

Introduce a `tgw-worker` system user (uid 901, gid 901).

- `tgw-worker` is added to the **supplementary group `tgw`** — granting it the group
  read bits on ItemData (`drwxr-x--- tgw:tgw 0750`, files `0640 tgw:tgw`).
- `tgw-worker` is **not the owner** of any ItemData path → write permission is denied
  at the kernel level.
- `tgw-http` continues to run as `tgw` (uid 900) and remains the sole process that can
  write ItemData.
- All `tgw-worker@.service` units change to `User=tgw-worker Group=tgw-worker`.
- Workers still need read on `config/`, `secrets/`, and `var/`; those are `0750
  tgw:tgw` so the group read bit covers them without any chmod changes.

No chmod changes are needed to ItemData itself — existing permissions are already
correct. The only missing piece is the separate worker OS user.

### Layer 2 — Internal fence client (`tgw.apis.fence`)

A lightweight HTTP client that workers use in place of `atomic_write_json`.

```python
# src/tgw/apis/fence.py
def get_item(cfg, sku) -> dict             # GET  /api/items/{sku}
def patch_item(cfg, sku, fields) -> dict   # PATCH /api/items/{sku}  {fields: {...}}
def append_item(cfg, sku, op, data) -> dict  # POST /api/items/{sku}/append
def item_action(cfg, sku, action, **kw) -> dict  # POST /api/items/{sku}/action
def ebay_write(cfg, sku, **blocks) -> dict # POST /api/items/{sku}/ebay-write
```

Auth: workers send the existing `api_key` from config as `Authorization: Bearer <key>`.
No new secret needed.

Base URL: `http://127.0.0.1:7373` (localhost only; never exposed externally).

### Layer 3 — New fence endpoints

Most worker writes map to the existing `PATCH /api/items/{sku}`. The gaps are
**list-append operations** and **safe ebay block merges**:

**`POST /api/items/{sku}/append`** — typed list appends:

```json
{ "op": "vision_result | photo | history_event | price_event", "data": { ... } }
```

The fence validates the op type, injects `appended_at` timestamp, and appends atomically
to the correct list field. Workers never build the list themselves.

**`POST /api/items/{sku}/ebay-write`** — deep-merge eBay blocks:

Merges `ebay_offer`, `ebay_listing`, `ebay_submitted` in one atomic op, preserving
sub-fields workers must not clobber (`price_comps`, `staged_at`, `photo_verify`).
Returns `{ok, sku, changed_fields}`.

**`POST /api/items`** — item creation through the fence (intake workers):

Exposes `items.create_item()` so `bundle_intake` and `multi_intake` never touch the
filesystem directly.

### What the fence gains for free

- **ebay_draft raw output**: `patch_item({draft_listing: {..., raw_prompt: ..., raw_response: ...}})` — one extra key, no new endpoint.
- **ebay_price history**: `append_item(price_event)` on every price write — fence enforces it; currently missing entirely.
- **Conflict detection**: `ebay-write` can compare incoming `live_price` against stored value and log divergence when a Seller Hub edit would be overwritten.
- **Full write audit**: one place to add a write log. Every mutation flows through `patch_item` or `ebay-write`.

### Layer 4 — Worker migration map

| Worker | Call sites | Fence replacement |
|--------|-----------|-------------------|
| `ai_identify` | 1 | `patch_item` (fields) + `append_item(vision_result)` |
| `ebay_draft` | 2 | `patch_item({draft_listing, raw_prompt, raw_response})` |
| `ebay_upload` | 1 | `append_item(photo, {local, url})` per photo |
| `ebay_stage` | 2 | `ebay_write(ebay_offer, ebay_submitted)` |
| `ebay_publish` | 2 | `ebay_write(ebay_listing, ebay_offer)` + `append_item(price_event)` |
| `ebay_price` | 1 | `ebay_write(ebay_offer)` + `append_item(price_event)` |
| `ebay_price_reducer` | 2 | `ebay_write(ebay_offer)` + `append_item(price_event)` |
| `ebay_sync` | 1 | `ebay_write(ebay_listing, ebay_offer)` |
| `ebay_repush` | 1 | `ebay_write` |
| `bundle_intake` | 1 | `fence.create_item` |
| `multi_intake` | 2 | `fence.create_item` + `patch_item` |
| `ebay_sku_migrate` | 3+ | `patch_item` (field updates); dir rename stays as `tgw` user — see Open Questions |
| `ebay/pull.py` | 4 | `ebay_write` or `patch_item` |
| `ebay/snapshot_backfill.py` | 1 | `patch_item` |

---

## Files to change

### NixOS flake (`~/tgw-flake`) — operator applies after plan approval

| File | Change |
|------|--------|
| `nix/tgw/users.nix` | Add `tgw-worker` user (uid 901, gid 901, extraGroups `["tgw"]`); add `tgw-worker` group (gid 901) |
| service unit template | Change `User=tgw` → `User=tgw-worker`, `Group=tgw` → `Group=tgw-worker` for `tgw-worker@.service` |

### Python source (`trader-grims-warehouse`)

| File | Change |
|------|--------|
| `src/tgw/apis/fence.py` | **New.** `get_item`, `patch_item`, `append_item`, `item_action`, `ebay_write` |
| `src/tgw/http_server.py` | Add `POST /api/items/{sku}/append` and `POST /api/items/{sku}/ebay-write`; add `POST /api/items` creation endpoint |
| `src/tgw/workers/ai_identify.py` | Replace `atomic_write_json` → fence calls |
| `src/tgw/workers/ebay_draft.py` | Replace → fence calls; add raw_prompt/raw_response to patch |
| `src/tgw/workers/ebay_upload.py` | Replace → `append_item(photo)` |
| `src/tgw/workers/ebay_stage.py` | Replace → `ebay_write` |
| `src/tgw/workers/ebay_publish.py` | Replace → `ebay_write` + `append_item(price_event)` |
| `src/tgw/workers/ebay_price.py` | Replace → `ebay_write` + `append_item(price_event)` |
| `src/tgw/workers/ebay_price_reducer.py` | Replace → `ebay_write` + `append_item(price_event)` |
| `src/tgw/workers/ebay_sync.py` | Replace → `ebay_write` |
| `src/tgw/workers/ebay_repush.py` | Replace → `ebay_write` |
| `src/tgw/workers/bundle_intake.py` | Replace → `fence.create_item` |
| `src/tgw/workers/multi_intake.py` | Replace → `fence.create_item` + `patch_item` |
| `src/tgw/workers/ebay_sku_migrate.py` | Replace field writes → `patch_item`; dir ops stay `tgw` user (see Open Questions) |
| `src/tgw/ebay/pull.py` | Replace 4 × `atomic_write_json` → `ebay_write` / `patch_item` |
| `src/tgw/ebay/snapshot_backfill.py` | Replace → `patch_item` |
| `tests/test_fence.py` | **New.** Unit tests: fence client (mock HTTP) + new endpoints (FastAPI TestClient) |
| `tests/test_invariants_items_fence.py` | Extend: grep audit that no file in `workers/` or `ebay/` imports `atomic_write_json` |

---

## Implementation sequence

Multi-session. Do not attempt all at once.

**Session A — fence endpoints + client (no OS change, workers unchanged)**
1. New endpoints in `http_server.py`: `/append`, `/ebay-write`, item creation
2. `src/tgw/apis/fence.py` client
3. Tests; existing suite still passes; `tgw health` green

**Session B — worker migration (workers still run as `tgw`, fence calls work)**
1. Migrate workers one at a time in queue order; restart each after change
2. Verify each worker succeeds end-to-end before moving to the next
3. Add grep audit to CI: `atomic_write_json` banned in `workers/` and `ebay/`

**Session C — OS lockdown (NixOS flake)**
1. Add `tgw-worker` user/group to `~/tgw-flake/nix/tgw/users.nix`
2. Update service unit template
3. `sudo nixos-rebuild switch --flake ~/tgw-flake#tgw-prod`
4. Verify all workers active, `tgw health` green, write attempt as tgw-worker fails

**Pre-Session A — eBay data recovery (one-off, runs as `tgw` user)**
1. Backfill offer_id + listing_id + price + EPS URLs for all 19,366 Inventory API items
2. Identify ~196 Trading-API-only listings (GetMyeBaySelling diff vs Inventory API SKU set);
   pull their title/price/condition/aspects from Trading API into local JSON
3. Migrate Trading-API-only items to Inventory API: `ebay_stage` → `ebay_publish` → end old
   listing. **Dave approves batch policy before running** — watchers and watcher count are lost
   in the relist gap; this cannot be undone.

**Session D — ongoing sync (now unblocked)**
1. Restart `ebay_sync` worker — data flows through fence → full audit trail going forward

---

## Acceptance criteria

- [ ] `grep -r "atomic_write_json" src/tgw/workers/ src/tgw/ebay/` → zero hits
- [ ] `grep -r "_write_field" src/tgw/workers/` → zero hits
- [ ] All `tgw-worker@*` services run as uid 901 (`tgw-worker`)
- [ ] `tgw-http.service` runs as uid 900 (`tgw`)
- [ ] `sudo -u tgw-worker touch /opt/TGW/data/ItemData/probe` → permission denied
- [ ] `sudo -u tgw-worker cat /opt/TGW/data/ItemData/<any-sku>/<sku>.json` → succeeds
- [ ] `POST /api/items/{sku}/append` with `op=vision_result` appends and returns `{ok: true}`
- [ ] `POST /api/items/{sku}/ebay-write` merges without clobbering `price_comps` or `staged_at`
- [ ] `ebay_draft` persists `raw_prompt` and `raw_response` in item JSON
- [ ] `ebay_price` appends a `price_event` to `price_history[]` on every price update
- [ ] All 19+ workers reach `active (running)` post-lockdown
- [ ] `tgw health` green (excluding pre-existing NATS warn)
- [ ] Local catalog reflects 19,653 live eBay listings (Seller Hub count as of 2026-06-28),
  minus any items sold during the recovery window. Breakdown: 19,366 Inventory API + ~196
  Trading-API-only + ~91 currently unaccounted (gap between API counts and Seller Hub total —
  identification script will resolve). Any remaining gap after migration is a data integrity
  issue requiring investigation before workers restart.
- [ ] Existing test suite (563+) continues to pass

---

## Open questions

1. **Intake worker filesystem access** — `bundle_intake` and `multi_intake` create new
   item directories. Directory creation requires write on the ItemData root. Options:
   (a) expose `POST /api/items` through the fence and have the API create the dir
   (cleanest — recommended); (b) keep intake workers as `tgw` user only (they don't
   modify existing items). Dave to decide.

2. **`ebay_sku_migrate` directory rename** — this worker renames SKU directories (create
   new dir, move files, delete old). Requires write on ItemData. Since this is a
   one-time migration tool rather than an ongoing pipeline worker, it could stay as
   `tgw` user via a dedicated systemd override. Or expose a `POST /api/items/{sku}/rename`
   fence endpoint. Dave to decide.

3. **Secrets read access** — `tgw-worker` in supplementary group `tgw` gets group-read
   on `/opt/TGW/secrets/` (currently `0750 tgw:tgw`). Workers need OAuth token and eBay
   credentials. Is group-read on secrets acceptable, or should secrets be API-mediated
   (workers request token via fence rather than reading file directly)?

4. **Fence unavailable during deploy** — if `tgw-http` restarts mid-deploy, workers get
   `ConnectionRefused`. `classify_dead_letter` already retries `connectionerror` with 120s
   backoff — confirm this covers `http://127.0.0.1:7373` refused connections, or add an
   explicit fence-unavailable transient class.

5. **Test mock strategy** — current worker tests mock `atomic_write_json`. After
   migration, mocks move to the HTTP layer. Confirm preference: `responses` library
   (intercepts `requests`) vs spinning up a FastAPI TestClient as a fixture.
