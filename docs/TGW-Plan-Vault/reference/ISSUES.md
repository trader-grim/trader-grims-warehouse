# TGW — Active Issues & Known Gaps

Living document. Add issues as discovered; close with date and resolution note.
Distinct from PP-* projects (which are planned features) — this tracks bugs,
incomplete wiring, and data quality problems that need fixing.

---

## Open Issues

### ~~ISS-001~~ — errorId 25002 Item.Country at publish ✅ RESOLVED 2026-06-11
- **Original symptom**: eBay rejects offer publish with 25002 for some Inventory API items
- **Fix**: added `availabilityDistributions` with `merchantLocationKey` to inventory item body
  (session 9). The three originally affected items (cooking/serving utensil categories 20649,
  137750) are now **live via Inventory API**.
- **Diagnosis correction**: later 25002 dead-letters (session 23) were mis-filed as ISS-001 but
  were actually item-specifics validation errors ("Contaminant Removal" value too long, "Model"
  missing) for an unrelated SKU (Water Filters, category 20684). Those items are already live
  via Trading API. All 15 stale dead-letters (ebay_stage ×13, ebay_publish ×2) cleared 2026-06-11.

### ISS-002 — 10 legacy items with wrong shipping profile (FRE instead of FC4)
- **Symptom**: migrated with eBay Standard Envelope profile instead of FC4
- **Affected**: categories 7317 (Game Pieces) + 261068 (Action Figures)
- **Item IDs**: 327195083346, 327195083374, 327195083408, 327195083423,
  327195083451, 227372145582, 327195085940, 227372145665, 227372145712
- **Fix**: manual Seller Hub edit per listing (Listings → Edit → Shipping → FC4)
- **After the edits**: run `sudo -u tgw tgw ebay-pull` to refresh the local mirror, then
  spot-check one item's `ebay_listing` block to confirm the change is reflected — manual
  Seller-Hub changes never sync back faster than the 6 h cycle, and this applies to ANY
  human edit made directly on eBay, not just this issue
- **Status**: pending operator action

### ~~ISS-003~~ — full_catalog_path config mismatch ✅ RESOLVED session 29
- **Was**: `load_config()` defaulted to `tgwcatalog.json`; JSON config had `master-catalog.json`
- **Fix**: changed code default to `master-catalog.json` (`config.py:63`); 2 tests in
  `tests/test_config_hygiene.py` assert default and explicit-override behaviour

### ~~ISS-004~~ — ebay_sku_migrate config bypasses load_config ✅ RESOLVED session 29
- **Was**: `ebay_sku_migrate` block not surfaced in normalised config dict (pre-a540d9b)
- **Reality**: already fixed in a540d9b — `load_config()` line 190 returns the block;
  worker reads `self.config.get('ebay_sku_migrate', {})` (not `cfg['raw']`)
- **Tests**: 3 tests in `tests/test_config_hygiene.py` cover presence, default, and
  round-trip read without reaching into `cfg['raw']`

### ISS-005 — dev_id missing from ebay-credentials.json
- **Symptom**: SOAP notification signature verification is incomplete without `dev_id`
- **Impact**: PP-SOLD-001 Tier 4 webhook rejects all signed notifications until dev_id is added
- **Code fix**: DONE 2026-06-12 — `verify_notification_signature` now rejects (not accepts) when
  `dev_id` is absent; accept-when-unsigned interim removed; test updated accordingly
- **Remaining operator action**: add `"dev_id": "..."` to `/opt/TGW/secrets/ebay-credentials.json`
  — value from developer.ebay.com → My Account → Application Keys → DevID field
  — required before deploying webhook infra (admin #16)
- **Status**: code done; pending operator credentials update

### ~~ISS-006~~ — _USER_PROMPT_ENRICHED not wired ✅ RESOLVED (verified session 15, 2026-06-07)
- **Was**: `workers/ai_identify.py` had the `_USER_PROMPT_ENRICHED` template but the wiring to
  PP-LOOKUP-001 was thought unfinished
- **Reality**: already fully wired. `ai_identify.py:171–182` calls `lookup_product()`, builds
  `product_context`, and `:199–204` selects `_USER_PROMPT_ENRICHED.format(product_context=...)`
  when context is present (`prompt_type='enriched'` recorded at `:271`). Stale issue — closed.

### ~~ISS-007~~ — GitHub linting errors ✅ FIXED 2026-06-04
- 70 errors auto-fixed by ruff; 5 manual fixes applied:
  - `trading.py`: added `from datetime import datetime` (used in type annotations)
  - `ebay_publish.py`: added `from tgw.apis.ebay.client import ebay_put`
  - `ebay_publish.py`: added `from tgw.ebay.pricing import to_99`
  - `sku_migration.py`: removed unused `new_json` variable assignment
- `ruff check src/ tests/` now passes clean

### ISS-009 — eBay refresh token dead (HTTP 400) — operator action required
- **Symptom**: `token_refresh` worker hits eBay `/identity/v1/oauth2/token` and receives HTTP 400 (invalid_grant)
- **Root cause**: eBay refresh token invalidated (likely from scope change during session 6). Additionally a double-buffer bug delayed the actual refresh call until the last 5 minutes of token life — fixed 2026-06-06
- **Current state**: token expired 2026-06-05 17:05; 4 dead_letter jobs in queue; worker process alive but idle
- **Fix (operator)**:
  1. `sudo -u tgw python3 /opt/TGW/src/trader-grims-warehouse/src/tgw/apis/ebay/get_access_token.py` — browser OAuth re-consent flow; writes fresh token to `secrets/ebay-token.json`
  2. `sudo -u tgw tgw restart-ebay-token` — clears dead_letter jobs, enqueues fresh token_refresh immediately
- **Status**: awaiting operator re-consent

### ISS-010 — needs_photos count inflated on home dashboard
- **Symptom**: home dashboard shows 33k+ items needing photos; actual un-photographed count is much lower
- **Root cause**: catalog `image` column is empty for many items that do have thumbnails — `thumbnail_gen` updates the SQLite `image` col but may be stale; catalog rebuild may not be propagating thumbnail presence correctly
- **Fix**: investigate `catalog_export.py` / `thumbnail_gen` image-col write path; run a full `tgw build-thumbnails` + `tgw build-all` to resync; check `needs_photo` filter in `http_server.py` dashboard endpoint
- **Status**: open

### ISS-011 — inventory browse prices display as $NaN
- **Symptom**: `/form/items` inventory browse shows `$NaN` in price column for many/all items
- **Root cause**: `price` column stores empty string `''` for unpriced items; `parseFloat('').toFixed(2)` is `NaN`; old `!= null` guard doesn't catch empty strings
- **Fix**: `_cardHtml` JS changed to `parseFloat(it.price)` + `isNaN()` guard → shows `—` for null/empty/non-numeric (`http_server.py:1986`)
- **Status**: fixed 2026-06-15

### ISS-012 — web home page health checks and recent activity not displaying
- **Symptom**: `/form/` home page — health status strip and recent activity section blank or missing
- **Root cause**: likely `GET /api/health` or `GET /api/dashboard` returning unexpected shape; or frontend JS failing silently
- **Fix**: check `/api/health` (Bearer-auth required?) and `/api/dashboard` responses from the browser; fix auth or response-shape mismatch in the home-page frontend (todo #870)
- **Status**: open

### ISS-008 — legacy_listing_resolved items may still have active listings
- **Symptom**: items marked `legacy_listing_resolved: True` may still have active eBay
  listings from before the Inventory API migration
- **Impact**: potential duplicate listings; `ebay_stage` active listing guard catches
  new cases but underlying data is not authoritative
- **Fix**: PP-SYNC-001 sync pass to pull authoritative active listing state from eBay
- **Status**: blocked on PP-SYNC-001 implementation

### ISS-013 — alt-text rename broke photo display order ✅ CLOSED 2026-06-19
- **Symptom**: After `tgw alt-text <sku>`, the original display photo is renamed to `<sku>-alt.jpg`; the gallery now shows a "random" photo first (often the second photo), with no way to control display order
- **Root cause**: todo #38 implementation renamed the original photo to `<sku>-alt.jpg` instead of creating a companion file. The intent was `<sku>-alt.jpg` = a new AI-generated derivative; the original should keep its filename
- **Fix**: `scripts/photo_repair_iss013.py` renamed all misnamed `<sku>-alt.jpg` files to `<original-photo>-alt.jpg` (rename-only, originals untouched). 618 items repaired 2026-06-19. Code fix already in via commit `9319e5e` (copy not rename). Originals remain alongside companions; archive sweep deferred until transactional base (Stage 2) is in place.
- **Status**: closed

### ISS-014 — qty field can be negative (data quality)
- **Symptom**: at least one item has qty = -1 (e.g. tgw202604041746293); inventory logic assumes qty ≥ 0
- **Root cause**: no validation guard in item write path for qty field
- **Fix** (todo #885): add guard in `items._write_field()` refusing qty < 0; `catalog-verify` rule `negative_qty` (critical); `tgw data-scrub --pass 3` repair (set qty=1 for any item with qty < 0)
- **Status**: open

### ISS-015 — Best Offers API rate limit exceeded
- **Symptom**: `/form/offers` shows "Trading API GetBestOffers failed: Your application has exceeded usage limit on this call"
- **Root cause**: Trading API call budget exhausted; no rate limiting or retry in `get_best_offers()`
- **Fix** (todo #886): call `GetAPIAccessRules` to surface call limits; add per-call rate limiting + exponential backoff; display friendly error with call-budget info in the UI
- **Status**: open

---

## Closed Issues

### ✅ errorId 25709 — Missing Content-Language header
- **Fixed**: Content-Language: en-US added globally to all Inventory API PUT/POST calls
- **Date**: 2026-06-03

### ✅ errorId 25021 — Invalid condition for category
- **Fixed**: `best_condition()` same-or-worse fallback; retry with USED_EXCELLENT in stage/publish
- **Date**: 2026-06-03

### ✅ recover_expired_jobs() not promoting retry_wait jobs
- **Fixed**: state machine bug fixed; retry_wait jobs now promoted back to queued when not_before passes
- **Date**: 2026-06-03

### ✅ Offer PUT before publish stripping fields
- **Fixed**: removed pre-publish offer PUT from ebay_publish (PUT is full-replace, was stripping condition/specifics)
- **Date**: 2026-06-03
