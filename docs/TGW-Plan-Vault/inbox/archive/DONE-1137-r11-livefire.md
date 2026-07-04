# DONE — #1137 PP-LISTEDITOR-001 R1.1 live-fire

Price-only delta test, tgw201501021970128 ($7.99 -> $8.49 -> reverted),
via revision.py's drift-gated apply path. Live-verified with fresh
uncached eBay API reads (GET /sell/inventory/v1/offer/<id>) in both
directions -- not just job-succeeded status, the real listing price. 
revision_history correctly recorded delta, baseline_hash, and the exact
API call made. hash_match=true, zero drift both times.

R1.1 gate is now CLEAR for PP-LISTEDITOR-001. Next step per the design:
wire the Update-Item button to this same apply path.

Found a real minor bug along the way (todo #1138): tgw revise --set's
help text claims dotted-path support (draft_listing.price) but the
live-apply path only accepts bare field names -- corrected by using
price=X directly; the wrong format fails loudly with a clear error, not
silently.
