## PP-EBAY-SNAPSHOT-001 — eBay Submitted Payload Capture & Photo Integrity

**Opened:** 2026-06-16 (session 34)
**Status:** Phase 1 DONE; Phases 2–4 + backfill pending (todos #891–894)
**Core principle:** Every payload we push to eBay is saved locally as `ebay_submitted`.
This gives us a full audit trail and the ability to re-push after eBay data loss.

### What it covers

`ebay_submitted` = snapshot of the exact inventory_item + offer payload sent to eBay by
`ebay_stage`. Stored in item JSON alongside `ebay_live` (raw eBay mirror) and `draft_listing`
(editor staging area). Distinct from PP-DATA-OWN-001 (pulling eBay's current view) — this
captures *what we sent*, enabling photo integrity checks and emergency re-push.

### Phases

- **Phase 1 — Save submitted payload** ✅ DONE (session 34):
  `ebay_stage` writes `ebay_submitted.{inventory_item, offer}` + `fulfillment_policy_id` to
  item JSON on each successful stage call.

- **Phase 2 — Verify photos after publish** (todo #891):
  After `publish_offer()` succeeds in `ebay_publish.py`, GET
  `/sell/inventory/v1/inventory_item/{sku}` from eBay; compare `imageUrls` against
  `ebay_submitted.inventory_item.product.imageUrls`; save
  `ebay_listing.photo_verify = {submitted_count, confirmed_count, verified_at}`;
  log warning if counts differ. One extra GET per publish, no new scopes needed.

- **Phase 3 — Periodic photo integrity check** (todo #892):
  In `ebay_sync`: every N days (config: `ebay_verify_interval_days`, default 7), GET
  inventory_item and compare imageUrls vs `ebay_submitted`. If photo count drops → log
  error + enqueue `ebay_repush` job that re-PUTs inventory_item from `ebay_submitted`.
  Automated defense against the eBay photo-deletion incident.

- **Phase 4 — tgw ebay re-push CLI** (todo #893 — DONE 2026-06-16):
  `tgw ebay re-push <sku>` re-PUTs inventory item using `ebay_submitted.inventory_item` as
  payload. `--all-listed` re-pushes every item with `ebay_listing.status=Active`. Nuclear
  option for mass eBay data-loss recovery.

- **Back-fill** (todo #894):
  ~23K listed items have no `ebay_submitted` block and no `ebay_offer.fulfillment_policy_id`.
  Back-fill approach: GET inventory_item from eBay per SKU with `ebay_listing.listing_id`,
  save as `ebay_submitted`; rate-limit to avoid API throttle. Scope: items where
  `ebay_submitted` is absent and `ebay_listing.listing_id` is present.

### Config key

`ebay_verify_interval_days` (int, default 7) — interval for Phase 3 periodic check.

---

