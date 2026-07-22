# Note: EBAY-DS-1077 reply rewritten in full

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T00:30Z
**Todo:** #1077

New long-form rewrite of the EBAY-DS-1077 support reply (case 260719-000018), replacing the shorter 2026-07-20 draft. Draft file: /home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup.txt -- still unsent, Dave to review/send.

Context: Dave asked for the full diagnostic story rather than the short version -- how we identified the orphaned offer's SKU as belonging to one specific local item of ours (searched historical records for any old identifier violating eBay's own 50-char/alphanumeric rule; exactly one match), the title/location/photo-file-id field scramble that item's own history shows actually happened (not just asserted), and a byte-level check ruling out hidden whitespace/encoding tricks as an alternative explanation -- laid out alongside every API/UI path already exhausted (direct lookup, %20/+/double-encoding variants, no-sku-param check, bulk fetch, DELETE attempt, full 98-page/19,509-item inventory sweep, no offerId ever returned, no legacy Item# on file, Seller Hub UI cross-check). Kept to per-item technical facts and plain business impact -- no internal system/worker/automation architecture named, per standing eBay minimal-disclosure practice.

EXTERNAL-SUPPORT-TICKET-REGISTER.md's EBAY-DS-1077 row already points at this file.
