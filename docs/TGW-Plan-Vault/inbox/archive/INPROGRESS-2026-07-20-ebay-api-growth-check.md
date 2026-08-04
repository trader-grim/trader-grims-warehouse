# In progress: 2026-07-20 — eBay API growth check, catalog freshness (Track E), paused on Dave's budget check

## Session arc

1. eBay Growth Check requirements relayed by Dave → real API-volume push.
2. alt-text backlog (500 items) enqueued and ran.
3. `ebay_sync` found missing from the NixOS flake entirely (not just
   stopped) — restored via `nix-flake-maintainer`, confirmed live
   (commit `b956251`, generation 94), draining its 30-job backlog on the
   known 25707 fallback.
4. EBAY-DS-1077 (orphaned-offer/25707) followup given urgency framing,
   register updated — still Dave's to send.
5. Standing Growth Check strategy encoded in `TGW-Master-Plan.md`
   (PP-EBAY-SNAPSHOT-001) and `EXTERNAL-SUPPORT-TICKET-REGISTER.md`: file a
   rate-increase request promptly whenever real legitimate load hits a
   documented ceiling. Dave having Tigwa obtain the actual Growth Check
   form.
6. Full planning doc written: `docs/ai-plans/ebay-api-growth-unblock.md` —
   Track A (build the missing `MARKETPLACE_ACCOUNT_DELETION` webhook, the
   real gate on any Growth Check submission — spec exists, zero
   implementation), Track B (25707/eBay-only wait), Track C (`ebay_legacy_sync`
   root-cause, explicitly NOT bundled with A/B), Track D (send-ready tickets
   1591/1592/1593), Track E (apply *existing* tooling more broadly to the
   existing catalog per Dave: "keep doing what we are doing but apply more
   to the existing items. More data. Better listings." — `PP-PROMO-001`
   sale events already built but never configured/run, `tgw bulk`
   (PP-BULKEDIT-001) already does bulk description edits, alt-text/
   ai_identify re-run sweeps use existing workers, inactive-listing repair
   UI already exists and just needs a regular surfaced view).
7. **Paused here** — Dave: "a lot of these exist already and the seller hub
   audit will give us more candidates. I have to look at my api budget."
   No further action taken; flagged that reprocessing existing items
   (ai_identify/alt-text backlog sweeps) is the known real cost driver
   (steady-state new-item cost ~$0.001/item; reprocessing is where spend
   adds up) — worth sizing before running.

## Nothing currently running

`ebay_sync` worker is live and draining its queue on its own (systemd,
no session dependency). No other background agents active.

## Next session should

- Wait for Dave's go-ahead on which Track E item to start with (or on
  Track A's webhook build) — do not start any of it unprompted.
- Check in on the Seller Hub parity audit (#1465, reassigned to Tigwa) —
  Dave expects it to surface more Track E candidates.
- Todo #1595 (ebay_sync restore) and #1596 (planning session) both closed
  this session.

## If interrupted

Read `docs/ai-plans/ebay-api-growth-unblock.md` for the full plan before
resuming. No code changes have been made — only the flake change
(ebay_sync restoration, already merged/live) and doc/plan updates.
