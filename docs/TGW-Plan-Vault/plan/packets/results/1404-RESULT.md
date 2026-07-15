# Result: #1404 ebay_publish dead-letter "Brand is missing"

Status: done
Todo: #1404   PP: PP-DEADLETTER-001

Files touched: none (investigation-only; no code or data change required)

## Findings

**Isolated, and already self-resolved before this packet was written** — not
a data gap, not a systemic aspect-completeness gap.

Live evidence, `queue_jobs` history for `tgw202605051925361` (entity_id
filtered via `payload_json::text LIKE '%tgw202605051925361%'`,
`sudo -u tgw psql -U tgw state_machine`):

```
0997c45c ebay_publish dead_letter  attempt 2  2026-07-05 22:43:22 -> 22:44:07
  HardFailure: eBay rejected publish: "... item specific Brand is missing ..."
...
31f1f65e ebay_publish succeeded    attempt 1  2026-07-05 23:00:37 -> 23:00:40
b605c64e ebay_publish succeeded    attempt 2  2026-07-05 23:01:18 -> 23:02:14
```

Roughly 16 minutes after the dead-lettered failure, the exact same item —
same `draft_listing.item_specifics` (no `Brand` key, unchanged then and now
per `tgw get tgw202605051925361`), same `category_id` 38064 ("Porcelain")
— was staged and published successfully with **no code change, no data
change, and no operator edit to item_specifics** in between (the only
operator edit on this item that day, a price change, landed at 22:43:22,
*before* the dead-lettered failure at 22:44, not after). The item is
currently `PUBLISHED`/`ACTIVE` on eBay: listing 327248472450
(`ebay_listing.listing_status: "ACTIVE"`, confirmed via stored
`ebay_live` sync snapshot dated 2026-07-14, and via `tgw get`).

This means eBay's own publish-time item-specifics validation rejected
"Brand" as a hard requirement once and then accepted the identical payload
on retry — a transient eBay-side validation flake for this item/category,
not a persistent "Brand aspect genuinely required and missing" condition.
No fallback value (e.g. `"Unbranded"`) was ever needed or applied.

**Required-aspect completeness check — already exists, working as
designed.** `src/tgw/workers/ebay_draft.py` lines 490–505 already backfills
any required aspect the AI left blank, with `Unbranded` / `Does Not Apply`
/ `N/A` fallback candidates tried in order against the category's allowed
values, before an item is ever queued for staging/publish. This item's
cached taxonomy aspect list for category 38064 reported only 1 required
aspect total (`aspects_required_total: 1`, and it was filled —
`aspects_required_filled: 1`) — `Brand` was not flagged as required in the
cached taxonomy data ebay_draft.py had at draft time, so the backfill loop
correctly had nothing to do for `Brand`. The rejection came from eBay's
publish-time validator, one time, contradicting both the cached taxonomy
and the (successful) retry — i.e. this is evidence of eBay-side flakiness,
not of the local completeness check being wrong or missing.

**Pattern check (systemic vs. isolated):** `sudo -u tgw tgw dead-letter
--limit 500 | grep -i brand` across ALL queues returns zero other matches.
`tgw dead-letter --queue ebay_publish` shows only 3 total dead-letters, of
which this is the only Brand-related one and the only one for this SKU.
No other item shows this class of gap. Confirmed isolated.

## Acceptance (live)

1. Isolated (1 item), and already resolved by the pipeline's own normal
   retry/requeue cycle before this packet was even authored — no fix
   needed, no data correction performed (none was needed; the item was
   never actually missing Brand data that mattered, and it's live on eBay
   today).
2. N/A — no correction was needed. Verified via `tgw get
   tgw202605051925361`: `ebay_listing.listing_status: "ACTIVE"`,
   `ebay_offer.status: "PUBLISHED"`, live listing
   https://www.ebay.com/itm/327248472450.
3. N/A — not systemic; no required-aspect pre-flight subsystem proposed.
   The existing backfill in `ebay_draft.py` (lines 490-505) already covers
   the general case this packet worried about; this specific rejection was
   eBay-side transient, which no local pre-flight check could have
   prevented anyway (the local taxonomy cache correctly said Brand wasn't
   required).
4. No code changed — full offline suite not required per packet's own
   "if any code changed" condition. Not run.

Deviations from spec: none. Packet's built-in escape hatch ("a one-off data
correction is an acceptable outcome") applies, further reduced to "no
correction was even needed — already resolved by normal retry."

Out-of-scope findings filed: none. No new gap surfaced beyond what's
already covered by the existing `ebay_draft.py` backfill logic; not
recommending a new todo for eBay-side transient-validation flakiness on a
single non-recurring event.
