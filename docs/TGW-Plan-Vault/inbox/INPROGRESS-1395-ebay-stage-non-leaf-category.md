# In progress: todo #1395 ebay_stage non-leaf category dead-letters

Working in worktree `/opt/TGW/var/worktrees/1395-ebay-stage-non-leaf-category`
on branch `todo/1395-ebay-stage-non-leaf-category`.

Task: investigate 17 ebay_stage dead-letters with eBay error "category is
not a leaf category". Packet suspects the `category_id = '99'` fallback in
`ebay_draft.py` (~line 326) or a non-leaf-returning `best_category()`.
Step 1: pull real ebay_category_id values from the 17 affected SKUs to
confirm which cause is real, per packet spec. Then fix per spec 2/3.
