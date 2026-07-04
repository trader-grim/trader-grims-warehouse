# DONE — todo #1054: item detail History link via sku_old

Added `GET /form/history/{sku_old}` — looks up a merged index built from
`historical-tgwcatalog.json` + `historical-master-catalog.json`, keyed by
each record's own `sku_old` field (confirmed live: this field matches
current items' `sku_old` exactly, no case-normalization guessing needed —
both files carry it, not just `sku`). Cached at module level for process
lifetime, matching the category-tree cache's no-auto-expiry convention
(these are static snapshot files).

Item detail's existing "SKU (old)" field now links to
`/form/history/<sku_old>`. Gated by the standard `/form/*` session-cookie
wall like every other form page (fixed a stale "no auth required" comment
along the way — that model predates the s42/43 session-cookie wall).

Live-verified against real production catalogs: 39,485 records indexed;
real item `tgw20140101144105453` (sku_old `TGW20140101144105453`) resolves
to its original title "Mumm Champagne Bottle Stopper Recorker Cork Napa
Beer Saver" correctly. 5 new tests. Full suite: 1790 pass / 1 skipped /
0 fail / 0 errors (was 1786).
