# DONE — #1124 PP-PHOTOSYNC-001 P8 canary probe

Dave designated the canary items live: "Simpsons Game of Life" board-game
replacement-part SKUs (6 found, all real published listings). Built
`scripts/photosync_canary_probe.py` — presses the real HTTP action
endpoint, waits for the job chain, ebay-pulls live state, diffs vs
intent, scans the journal window, reports pass/fail, notifies on red.

Live-verified end to end against `tgw201501021970068`: found and fixed
two real bugs along the way (wrong auth header — needs `Authorization:
Bearer <key>`, not `X-API-Key`; wrong live-state field shape — needs
`ebay_live.inventory_item.product.*` + `ebay_listing.live_price`, not
the flatter shape first assumed). Final run: clean PASS, shown live in
`tgw ops-digest`'s new `CANARY PROBE` line.

Red path verified via mocked status file (unit test), not a deliberate
live-listing corruption — flagged as a scoped, judgment-call deviation
from the literal acceptance spec. Daily timer deferred to 2pm (nix flake
change under the freeze, same as #1108/#1113/#1126).

4 new tests, full suite 1814 passed.
