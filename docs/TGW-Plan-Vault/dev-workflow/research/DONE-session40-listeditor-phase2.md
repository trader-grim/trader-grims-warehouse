# DONE — session 40: PP-LISTEDITOR-001 Phase 2 revision apply (todo #1084) — CODE COMPLETE, live-fire pending

Building revision apply: extract build_inventory_body/build_offer_body from
ebay/sync.py stage_draft(), fill the eBay PUT stub at revision.py:399, flip
_APPLY_ENABLED=True, add revise_apply action to http_server.py, tests in
test_revision.py with mocked ebay_put. Design confirmed by Dave this session
(the stub's activation condition). PP-ACTIONCONSOLE-001 design pass completed
earlier this session — all opens settled, recorded in master plan; this Phase 2
is its build prerequisite ("Update Item" = revision apply).
