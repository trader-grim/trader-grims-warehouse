# Runbook: sold-sync gaps (sold on eBay, still "available" locally)

**Failure mode:** an item sold on eBay but local state still shows it available — risking
re-list, double-handling, or oversell on multi-quantity flows. The reverse also appears
here: an item wrongly marked sold.

How sold detection works today (two paths into the same idempotent
`_mark_item_sold()`):

1. **Polling** — `ebay_legacy_sync` (Trading API `GetOrders`, 90-day windows, 365-day
   initial lookback; cursor at `/opt/TGW/runtime/state/ebay-sold-sync-state.json`) and
   `ebay_sync` (Inventory offers, every 6 h). **Up to ~6 h of lag is by design.**

   Operational expectation (per Dave, 2026-06-10): as the SKU migration completes,
   `ebay_legacy_sync` is a **safety net**, not a primary path — it should have little to
   nothing to do each sweep. Its job is catching items that reached eBay outside the
   Inventory API (some other tool, manual listing) and pulling them into the local
   mirror. It is fine for it to simply restart along with everything else after a
   failure; the thing to verify is that it is *actually performing the sweep* — an alive
   worker that never completes a sweep is the failure, not a missed restart window.
2. **Webhook** — `POST /webhooks/ebay/notification` on tgw-http. The webhook **always
   ACKs** even on internal error (to stop eBay retry storms), so a processing bug
   silently drops the event — polling is the safety net. Signature verification is
   incomplete without `dev_id` (ISS-005); ingress infra is not yet publicly deployed.

On sale, the item gets `status=sold` + an `ebay_sale` block; sync writes only
`ebay_*`/status mirror fields, never draft content.

## Symptoms

- Operator notices a sale (eBay app / Seller Hub) but `tgw get <SKU>` shows no
  `ebay_sale` block and `status` ≠ `sold` for **longer than ~6 h**.
- Sold items still appear in pick/list views and `tgw staged`-adjacent reports.
- `velocity_stats` / `tgw velocity-report` numbers flatline (no new sold records feeding
  them).
- An item is marked sold that didn't sell (webhook forgery/bug — see ISS-005; mitigated
  by the 10-min cached listing-id index check).

## Likely root causes

1. **`ebay_legacy_sync` / `ebay_sync` not running** — worker down, dead-lettered, or its
   self-schedule job lost (the dedupe singleton vanished without a successor).
2. **Token failure** — both sync workers degrade with all eBay workers
   ([ebay-token-failure.md](ebay-token-failure.md)).
3. **Sold-match miss**: matching is by `listing_id` — fails for unmigrated/archived
   legacy items (the ~22 K archive-tombstone gap is permanent and accepted; recent
   unmigrated listings are the actionable subset).
4. **State-file corruption/reset** — `ebay-sold-sync-state.json` damaged: the lookback
   window resets and the worker re-scans old orders (absorbed by idempotency; the
   symptom is a slow catch-up pass, not data damage).
5. **Webhook event dropped** (always-ACK behavior) — only matters once webhook infra is
   live; polling recovers within its cycle.
6. **Wrongly marked sold**: erroneous/forged notification, or a listing-id collision.

## Diagnosis

```bash
# 1. Are the sync workers alive and scheduled?
systemctl status tgw-worker@ebay_legacy_sync.service tgw-worker@ebay_sync.service
psql -U tgw state_machine -c "
  SELECT queue_name, state, run_at, not_before, updated_at
  FROM queue_jobs
  WHERE queue_name IN ('ebay_sync','ebay_legacy_sync')
    AND state NOT IN ('succeeded','cancelled')
  ORDER BY queue_name, created_at DESC;"
# expect exactly one active/queued self-schedule job per queue

# 2. When did each last complete?
psql -U tgw state_machine -c "
  SELECT j.queue_name, max(h.created_at) AS last_success
  FROM queue_job_history h JOIN queue_jobs j USING (job_id)
  WHERE h.new_state='succeeded'
    AND j.queue_name IN ('ebay_sync','ebay_legacy_sync')
  GROUP BY 1;"

# 3. Recent run logs (matches found, orders scanned, errors)
journalctl -u tgw-worker@ebay_legacy_sync.service --since "-12 hours"

# 4. The sync cursor
sudo -u tgw cat /opt/TGW/runtime/state/ebay-sold-sync-state.json | python3 -m json.tool

# 5. The specific item
sudo -u tgw tgw get <SKU>
# does it have ebay_listing.listing_id? (no listing_id = sold-match can't work)
# does it have a legacy 'Item number' instead? (unmigrated → match-miss class)

# 6. Webhook path (if deployed): recent notifications received
journalctl -u tgw-http.service --since "-24 hours" | grep -i notification
```

## Recovery

```bash
# Sync workers down, or self-schedule job lost → restart; each worker's startup
# enqueue restores the self-schedule when its queue is idle:
sudo systemctl restart tgw-worker@ebay_legacy_sync.service tgw-worker@ebay_sync.service

# Force an immediate mirror refresh (active listings + sold orders → ItemData):
sudo -u tgw tgw ebay-pull              # add --dry-run first to preview changes

# One known-sold item that polling missed (match-miss):
#   prefer fixing the listing_id linkage if it exists, then re-running sync /
#   tgw ebay-pull, so the automated path records the ebay_sale block itself.
#   If no linkage is possible (archived legacy listing), mark it through the fence:
sudo -u tgw tgw statusupdate sold <SKU>
#   and add the ebay_sale facts (order id, price, date) via HTTP PATCH / MC extfs
#   so velocity data stays honest.

# Corrupted state file → move it aside; next run re-scans the full lookback window
# (slow but safe — _mark_item_sold is idempotent):
sudo -u tgw mv /opt/TGW/runtime/state/ebay-sold-sync-state.json \
               /opt/TGW/runtime/state/ebay-sold-sync-state.json.bad
sudo systemctl restart tgw-worker@ebay_legacy_sync.service

# Wrongly marked sold → verify in Seller Hub FIRST (the listing may genuinely have
# ended), then revert through the fence:
sudo -u tgw tgw statusupdate "<previous status>" <SKU>
#   remove/annotate the bogus ebay_sale block via HTTP PATCH / MC extfs; note the
#   incident in reference/ISSUES.md (webhook validation is ISS-005 territory).
```

## Rollback

- Restarting sync workers / re-enqueueing sync jobs needs no rollback — sync writes only
  mirror fields and `_mark_item_sold()` is idempotent; a duplicate pass changes nothing.
- Deleted/reset the state file: the only cost is a long re-scan; the old file (if moved
  aside, not deleted) can be restored to resume the previous cursor.
- Manual sold-marks made in error revert with `tgw statusupdate` + removing the
  `ebay_sale` block. Because `velocity_stats` rebuilds nightly from ItemData, correcting
  the item also corrects the stats on the next run (or restart
  `tgw-worker@velocity_stats.service` to trigger its startup enqueue).

## Verification

```bash
# 1. Both sync queues each have one fresh success and one future self-schedule
psql -U tgw state_machine -c "
  SELECT j.queue_name, max(h.created_at) AS last_success
  FROM queue_job_history h JOIN queue_jobs j USING (job_id)
  WHERE h.new_state='succeeded'
    AND j.queue_name IN ('ebay_sync','ebay_legacy_sync') GROUP BY 1;"

# 2. The missed item is now correct
sudo -u tgw tgw get <SKU>     # status=sold + ebay_sale block (or reverted, if that
                              # was the incident)

# 3. Cross-check a few recent Seller Hub sales against local state — all present?
#    (spot-check 3-5 order IDs from the last 48 h)

# 4. Cursor advancing
sudo -u tgw cat /opt/TGW/runtime/state/ebay-sold-sync-state.json | python3 -m json.tool
# run again after the next cycle — the window/timestamp must move

# 4b. Sweep doing roughly the expected amount of work: near-zero changes per
#     legacy-sync run is HEALTHY (safety-net role). A sudden burst of changed items
#     means something is creating listings outside the Inventory API — find the
#     source, don't just absorb it.

# 5. Velocity pipeline fed
sudo -u tgw tgw velocity-report | head    # sold counts ticking up after next nightly run

sudo -u tgw tgw health
```
