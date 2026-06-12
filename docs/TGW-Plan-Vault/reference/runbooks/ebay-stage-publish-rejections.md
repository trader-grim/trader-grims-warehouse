# Runbook: eBay stage/publish rejections & listing-integrity incidents

**Failure mode:** the eBay Inventory API rejects `ebay_stage` (PUT inventory_item /
POST offer) or `ebay_publish` (POST offer/publish), or a listing-integrity invariant is
at risk (duplicate listing, stripped offer fields). Money is involved at this stage —
publish rejections dead-letter **by design** so a human decides.

Full error-code reference: `docs/TGW-Plan-Vault/reference/eBay-Error-Codes.md`.
Per-category quirks: `reference/CATEGORY-QUIRKS.md`.

## Symptoms

- Dead letters in `ebay_publish` or `ebay_stage` with eBay errorIds in `error_detail`.
- `tgw publish <sku>` returns ok but the item later dead-letters in the publish queue.
- An item appears twice on eBay (legacy listing + new Inventory-API listing).
- A live listing suddenly lost its condition/specifics/shipping fields (full-replace
  PUT regression — see below).

## Likely root causes

| errorId / pattern | Cause | Notes |
|---|---|---|
| **25021** condition not supported | Category accepts only conditionId 3000 ("Used") | HANDLED — auto-retry with `USED_EXCELLENT`. Dead-letter here means the fallback also failed (rare) — check `apis/ebay/conditions.py` for the category |
| **25002** Item.Country | Category requires explicit country binding | ISS-001. Fix applied (`availabilityDistributions` + `merchantLocationKey`); observed categories 34032, 14027, 13916. If it fires again: manual Seller Hub publish, update ISS-001 |
| **25709** missing Content-Language | `Content-Language: en-US` header missing on a PUT/POST | CLOSED — fixed globally. A recurrence means a new call site bypassed the client helper |
| **25500-series** strikethrough rejected | `originalRetailPrice` sent but account not approved | Keep `ebay.strikethrough_enabled=false` until `tgw strikethrough-check` confirms approval |
| `already active listing` | Stage guard tripped — item is already live | Correct behavior; run `tgw ebay-pull` to mirror status locally |
| `legacy eBay Item#` | Unresolved legacy listing (ISS-008 — resolution data not authoritative) | `tgw resolve-legacy <sku>`; verify on eBay before overriding |
| HTTP 400, unknown errorId | New eBay validation rule | Read full JSON in `error_detail`; handle per eBay-Error-Codes.md; add to that doc |

**Listing-integrity invariants (must never regress; tests in
`tests/test_invariants_*.py`):**

- **Offer PUT is full-replace** — every `PUT /sell/inventory/v1/offer/{id}` must send the
  complete body from `sync._build_offer_bodies()`. A partial body silently strips fields
  from the live offer (invariant C4; the reducer was the historical violator, fixed
  2026-06-10).
- **Never PUT an offer before publish** (closed bug; invariant lives in
  `workers/ebay_publish.py`).
- **Stage never publishes; publish is `tgw publish` only** (invariants C2/C3).

## Diagnosis

```bash
# 1. What exactly did eBay say?
sudo -u tgw tgw dead-letter --queue ebay_publish
psql -U tgw state_machine -c "
  SELECT payload_json->>'sku' AS sku, error_code, error_detail, updated_at
  FROM queue_jobs
  WHERE queue_name IN ('ebay_stage','ebay_publish') AND state='dead_letter'
  ORDER BY updated_at DESC LIMIT 20;"

# 2. The item's draft/offer state (JSON is truth)
sudo -u tgw tgw get <SKU>
# check: draft_listing.{category_id, condition_id, price}, ebay_photos,
#        ebay_offer.{offer_id, status}, ebay_listing.status, 'Item number' (legacy)

# 3. Cross-check the category's known quirks
grep -n "<category_id>" docs/TGW-Plan-Vault/reference/CATEGORY-QUIRKS.md

# 4. Duplicate-listing suspicion: what does eBay actually have?
sudo -u tgw tgw ebay-pull            # mirrors offer/listing status into item JSON
# then search Seller Hub for the SKU/title to confirm listing count

# 5. Stripped-fields suspicion: compare live offer to draft_listing
#    (Seller Hub → listing; or the ebay_sync mirror after a pull)
```

## Recovery

```bash
# Condition rejection that escaped the fallback (25021):
#   set draft_listing.condition_enum to the category's accepted value (usually
#   USED_EXCELLENT → conditionId 3000) through the fence — HTTP PATCH or the MC
#   tgwitem extfs (nested draft fields are not reachable via `tgw update`) — then:
sudo -u tgw tgw dead-letter --requeue <JOB_ID>

# 25002 Item.Country (ISS-001 categories):
#   publish manually in Seller Hub (the offer is staged and visible there),
#   then mirror state back:
sudo -u tgw tgw ebay-pull

# Duplicate listing (legacy + new both live):
#   END THE LEGACY LISTING in Seller Hub (keep the Inventory-API one — it carries
#   reprice_schedule + local mirror), then:
sudo -u tgw tgw resolve-legacy <SKU>
sudo -u tgw tgw ebay-pull

# Stripped offer fields (full-replace regression):
#   re-stage rebuilds the complete offer body from draft_listing:
sudo -u tgw tgw enqueue-sku ebay_stage <SKU>     # idempotent; PUTs full body to the
                                                  # existing offer_id
#   then find and fix the call site that sent a partial PUT — this is a code bug;
#   grep for '/offer/' PUTs outside sync._build_offer_bodies usage.

# Unknown new errorId:
#   do NOT blind-requeue; document it in reference/eBay-Error-Codes.md and
#   reference/ISSUES.md first, decide handling, then requeue.
```

## Rollback

- **Accidentally published** (item went live that shouldn't be): end the listing in
  Seller Hub immediately, then `tgw ebay-pull` so local state mirrors the ended listing.
  There is no automated unpublish in TGW — publishing is one-way by design.
- **Wrong price/fields went live**: fix `draft_listing`, then re-stage
  (`tgw enqueue-sku ebay_stage <SKU>` PUTs the full corrected body). For price-only,
  prefer letting the operator set the price and re-staging over manual Seller Hub edits,
  so local JSON stays canonical.
- **Re-staged the wrong item**: staging is idempotent and UNPUBLISHED — delete the offer
  in Seller Hub if truly unwanted; local `ebay_offer` block will correct on next
  `ebay_sync` / `tgw ebay-pull`.
- **ebay_sku_migrate left an item delisted** (crash between delist and relist): the
  rollback manifest is at `/opt/TGW/var/log/sku-migrate-*.json` and the rename is in the
  `sku_history` table — relist from the manifest data; the migration worker can be paused
  via its config block (`ebay_sku_migrate.enabled: false` in
  `/opt/TGW/config/tgw-api-config.json`) while you recover.

## Verification

```bash
# 1. Job completed, not re-dead-lettered
psql -U tgw state_machine -c "
  SELECT state, count(*) FROM queue_jobs
  WHERE queue_name IN ('ebay_stage','ebay_publish') GROUP BY 1;"

# 2. Item state correct locally
sudo -u tgw tgw get <SKU>
# staged:    ebay_offer.status = UNPUBLISHED, offer_id present
# published: ebay_listing.{listing_id, status=Active, listing_url},
#            reprice_schedule present with launch stage done_at stamped

# 3. eBay agrees (mirror refresh)
sudo -u tgw tgw ebay-pull
sudo -u tgw tgw staged          # staged items list sane

# 4. Exactly ONE live listing for the item (Seller Hub search by title/SKU)

# 5. For full-replace fixes: confirm condition + item specifics still present on the
#    live listing after the PUT (Seller Hub → view listing)
sudo -u tgw tgw health
```
