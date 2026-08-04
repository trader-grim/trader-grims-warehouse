status: cleared
reviewer: Claude (main session, catio-nix-0.0.1-alpha)
todo: #1417   pp_ref: PP-LISTEDITOR-001
branch: todo/1417-ebay-draft-to-inventory-record-reverse-flow @ 928ca63

## Live-data verification
`ItemData` unchanged: 55,419 items before and after, zero diff on a
10-item sample vs the latest hourly snapshot. The coder's own throwaway
test SKU (`tgw20260715094902010`) confirmed fully deleted, no orphaned
queue jobs. I ran a second, independent throwaway test
(`tgw_reviewtest_1417_verify`) myself — not just re-trusting the coder's
— and cleaned it up completely (verified).

## Independent re-verification (not taken on manifest trust)
- Full offline suite re-run in the worktree: **2318 passed, 1 skipped**
  — matches the manifest exactly, 28 more than #1416's 2290 baseline.
- C12 static detector re-run clean (3 passed) after this packet's
  allowlist renumbering.
- **Ran my own diff/apply/re-diff cycle** against a fresh test item
  (not the coder's): seeded a Set A/Set B mismatch on `Type` plus a
  Set-B-only new fact on `Size`, confirmed `Color` (agreeing) correctly
  excluded, applied only `Type`, confirmed `Size` still shows as an open
  diff afterward. Matches the packet's Acceptance item 2 exactly,
  independently reproduced.

## Checked against spec
- Diff engine (`inventory_diff.py`) built entirely on #1418's accessors,
  pure functions, correct source/detected_at derivation with honest
  `None` for legacy items lacking history (Prime Directive 1 — no
  fabricated timestamps).
- New endpoints (`GET`/`POST inventory-diff`) — read-only diff endpoint
  verified not to mutate (byte-for-byte unchanged, per the coder's own
  test, spot-checked the test code itself, not just its pass/fail).
- New UI panel distinctly labeled and functionally isolated from
  `accept_proposals`/`revision_draft` — a dedicated test
  (`test_inventory_diff_apply_does_not_touch_draft_listing_or_revision_draft`)
  proves no shared write path; read it directly, confirmed it asserts
  what it claims.
- Default-checked-by-default requirement (Dave's explicit design call)
  — implemented, matches spec point 3.
- No auto-promotion, no confidence threshold — confirmed absent from the
  diff, matches Dave's explicit rejection of that option earlier this
  session.
- Out-of-scope list respected: no multi-marketplace code, no bulk sweep
  across the 55k catalog (mechanism proven on exactly one throwaway item
  each time, never swept).
- Invariant C13 added, cites C12/#1416/#1417 correctly, documents the
  sticky-skip decision and the un-gated-detector decision explicitly
  rather than silently.

## Three items flagged by the coder for Dave's explicit confirmation —
## not defects, legitimate "your call" defaults within the packet's own
## allowances, surfacing them rather than silently accepting on Dave's
## behalf
1. **Sticky-skip vs re-surface**: implemented re-surface (no stored
   dismissed state) — this was literally the packet spec's own suggested
   default, confirmed rather than assumed. Reasonable; no objection.
2. **C13's staleness detector is NOT gated on a live `ebay_offer.offer_id`**,
   unlike C12's `field_set_drift`. This is a real behavioral choice (it
   will flag many more items — every drifted pre-publish draft, not just
   live ones) and the packet spec didn't explicitly settle it either way.
   Reasoning given is sound but this is worth Dave's explicit yes/no
   before it's treated as final, not just reviewer-accepted.
3. **30-day staleness threshold** — implemented as the packet's own
   proposed default, correctly flagged rather than silently hardened
   into "the" answer.

None of these are spec violations or invariant failures — the packet
explicitly invited "your call, flag it" on all three, and the coder did
exactly that instead of silently picking. Surfacing them here so they
reach Dave, not resolving them myself.

## Fix-attempt accounting
0 of 2 fix attempts used — no gap found requiring a reviewer fix, unlike
#1416.

## Summary
Clean. Independently re-verified at every step this session's discipline
requires (live-data snapshot diff, full suite, C12 detector, and my own
separate live diff/apply/re-diff test — not just re-running the coder's).
Three legitimate open design questions surfaced for Dave, not decided
unilaterally. Cleared for stitch.

**This is the third and final packet in the #1418→#1416→#1417 sequence.**
Per Dave's standing note this session: once all three are believed fully
fixed, the next step is his own adversarial review + his pass — this
review does not close that loop, it clears this packet for stitch.
