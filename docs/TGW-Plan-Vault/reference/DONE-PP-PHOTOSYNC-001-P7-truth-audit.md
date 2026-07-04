# DONE: PP-PHOTOSYNC-001 P7 — truth-audit rules (the liar detector)

Todo: #1123. Per packet spec in `plan/pp/PP-PHOTOSYNC-001.md`.

## Done
4 new catalog-verify rules, all live-verified against the real 55,419-item dataset:
- `photos_short_on_ebay` — Active inventory-API item, live photo URL count <
  on-disk count.
- `photo_verify_stale` — submitted/confirmed count mismatch, or verified_at
  predates the last stage.
- `submitted_live_drift` — submitted vs live field diff, ONLY when the live
  pull genuinely postdates the submission (encodes the exact timestamp-order
  discipline that avoided a false "eBay rewrote our data" conclusion earlier
  today).
- `success_count_contradiction` — journald scan for `ebay_upload_complete`
  events with `to_attempt>0` but `new==0` (added a `to_attempt` field to that
  event in P1 specifically so this check works structurally, not via
  free-text log parsing).

JSON sidecar on `--output` + new CATALOG-VERIFY section in `tgw ops-digest`
(cheap file read, never a fresh scan). Nightly systemd timer written into the
flake (`nix/tgw/backup.nix`, `tgw-catalog-verify-nightly`, 02:00 daily),
`nix flake check` clean for vm/a1131/tgw-prod — **NOT deployed**, needs Dave's
go for `nixos-rebuild switch` on tgw-prod (a live infra action, done separately
from source-repo work).

## Bug caught during live verification (important)
First version used `ebay_photos` as the live-photo-count proxy. All unit tests
passed. Live run against the full 55,419-item dataset produced **9,382 false
positives** — most of the historical catalog never populated that local
bookkeeping field even when photos were genuinely live (an older pipeline path
wrote `draft_listing.imageUrls` directly, skipping `ebay_photos`). Would have
made the ops-digest cry wolf every single night. Caught by actually running it
live instead of trusting green tests (exactly the PD4/P7 lesson). Fixed by
switching to `draft_listing.imageUrls`/`ebay_offer.photo_urls` — the same
methodology already validated at 492/9,403 earlier in the session. Corrected
live count: 491 (down 1, matching this morning's manual fix of
tgw202606021133367).

## Tests
`tests/test_catalog_verify.py` (+18 tests, including the false-positive
regression test `test_photos_short_on_ebay_ignores_items_with_no_recorded_urls`)
+ `tests/test_ops_digest_catalog_verify.py` (6, new). Full targeted suite:
127/127 green.

## Live verification (PD4)
`tgw catalog-verify --severity critical --output .../catalog-verify-nightly.md`
run against all 55,419 real items: 9,352 critical violations (8,300 no_title —
pre-existing rule, not new; 558 photo_verify_stale; 491 photos_short_on_ebay; 3
negative_qty). `tgw ops-digest` confirmed reading the sidecar and rendering the
new CATALOG-VERIFY section with correct counts. Log-scan rule confirmed 0
contradictions in the last 24h (expected — P1's fix prevents new ones).

## Status: COMPLETE (code side). Todo #1123 marked done.
## Outstanding: nix timer needs Dave's go to deploy (nixos-rebuild switch on
## tgw-prod) — written and eval-verified but not live yet.
## Next packet per plan: P9 (#1125, priority 28, next-highest in
## PP-PHOTOSYNC-001) or the parallel forward track #1122.
