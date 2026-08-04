Todo #1309 (PP-COHESION-001, invariant A4) — DONE, stitched. ready.py's
ready_pool() now reads item JSON through the tgw-api fence
(find_item_jsons + load_item_doc) instead of hand-building paths and raw
json.loads(). Beneficial side effect: pool now reports an item's canonical
sku field (rename-aware) instead of always trusting the directory name.
Reviewed clean, full suite green.
