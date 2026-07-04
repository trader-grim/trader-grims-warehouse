# INPROGRESS: PP-PHOTOSYNC-001 P4 — fleet photo repair (1→5→ramp)

Todo: #1119. Pre-authorized by Dave 2026-07-03 (1→5→ramp, no further gate needed
between phases). Scope source: today's fresh catalog-verify run —
`photos_short_on_ebay`, 491 items (corrected methodology from P7, NOT the stale
`ebay_photos`-field method the packet doc originally referenced).

## What I'm doing
n=1: repair one item via the real operator HTTP action endpoints (ebay_upload +
ebay_update, origin=operator so it bypasses the halted background EPS pool),
verify live via ebay-pull. Then n=5 with a before/after table for Dave. Then
enqueue the remaining ~485 as BACKGROUND jobs (no origin) — these will
legitimately defer under today's already-halted EPS pool (3517/5000 spent) and
resume automatically after tonight's reset via P1's quota-retry mechanism. That
deferral is by design, not a bug — the ramp must yield to operator work.

## Where I am
Starting n=1.

## Next step if interrupted
Check queue_jobs for `ebay_upload`/`ebay_stage` jobs with dedupe keys matching
the 491-item list (`/opt/TGW/var/run/catalog-verify-nightly.md`) to see how far
the ramp got. Re-run `tgw catalog-verify --severity critical` to get a fresh
`photos_short_on_ebay` count vs the 491 baseline — the delta is repair progress.

## What actually happened
n=1 (tgw20160122242616788) revealed the ENTIRE 491-item P4 population is
blocked by ebay_stage's legacy-listing guard — and a bulk cross-check against
the live Inventory API (read-only, all 491) showed 100% are genuinely
Inventory-managed on eBay's side despite a stale local "Item number" field.
Dave connected this to a known cause: occasional Seller Hub use during the
month-long Inventory-API migration gap, and gave a standing instruction to
persist findings + build a live-verified auto-repair path, not just detect.

## Built (PP-PHOTOSYNC-001 P10, todo #1128, DONE)
- ebay_stage.py: legacy guard moved before the C9 gate, persists
  `legacy_listing_blocked` durably on every hit (operator or background).
- tgw.ebay.pull.check_legacy_duplicate_listing(): live Inventory-API offer
  lookup, compares listingId against the local record. Only an
  operator-origin force-update runs this and can auto-resolve on a confirmed
  match, falling through to the normal staging path (the actual repair).
- cmd_resolve_legacy now runs the same check by default (--force to bypass).
- New catalog-verify rule: legacy_listing_unrepaired.
- Dead end, kept but not auto-invoked: revise_item_pictures() (Trading API
  photo revise) — live-tested, eBay rejected it, proving these items are
  Inventory-managed not Trading-managed (the opposite of the guard's premise).
- Incidental fix found live: apis/ebay/catalog.py lookup_epid() only treated
  401/403 as "scope not granted" — eBay returns 400 for a never-granted
  scope. Any staging attempt on a barcoded item retried forever before this.
- Encoded as invariant C11 (invariants.md) + a standing rule in CLAUDE.md
  settled-architecture section, per PD5.

## Live verification (PD4)
Full loop confirmed on the real n=1 item across several iterations: durable
persistence -> duplicate check (match=true) -> auto-resolve -> fall-through
-> EPID-skip fix -> correct dead-letter on an UNRELATED per-item business
conflict (Best Offer + multi-marketplace, needs Dave's call, not code).

## Tests: 1486 passed / same 9 pre-existing failures+18 errors as baseline.

## Status: P10 COMPLETE. P4 remains PAUSED — need a clean n=1 (no incidental
## per-item conflicts) for a real before/after demo, and Dave's decision on
## the Best-Offer/multi-marketplace item found during this test.
