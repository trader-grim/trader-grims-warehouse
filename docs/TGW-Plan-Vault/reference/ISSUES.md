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

### ISS-003 — full_catalog_path config mismatch
- **Symptom**: `tgw-api-config.json` sets `full_catalog_path` to `master-catalog.json`
  but `load_config()` defaults to `tgwcatalog.json`; code default wins silently
- **Risk**: if `full_catalog_path` is ever read from the JSON value, wrong file is used
- **Fix**: align JSON value to match code default, or remove the key from JSON so the
  default is clearly canonical
- **Status**: low urgency; no production impact currently

### ISS-004 — ebay_sku_migrate config bypasses load_config
- **Symptom**: `ebay_sku_migrate` block in JSON is read via `cfg['raw']` directly;
  not surfaced in the normalised config dict like all other keys
- **Risk**: inconsistent pattern; easy to miss when auditing config
- **Fix**: add `ebay_sku_migrate` dict to `load_config()` return dict
- **Status**: low urgency

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

### ISS-008 — legacy_listing_resolved items may still have active listings
- **Symptom**: items marked `legacy_listing_resolved: True` may still have active eBay
  listings from before the Inventory API migration
- **Impact**: potential duplicate listings; `ebay_stage` active listing guard catches
  new cases but underlying data is not authoritative
- **Fix**: PP-SYNC-001 sync pass to pull authoritative active listing state from eBay
- **Status**: blocked on PP-SYNC-001 implementation

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
