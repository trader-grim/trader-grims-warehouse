status: cleared (after 1 bounded fix, within cap)
reviewer: Claude (main session, catio-nix-0.0.1-alpha)
todo: #1416   pp_ref: PP-LISTEDITOR-001
branch: todo/1416-inventory-record-ebay-draft-set-boundary @ a8143cd (fix
        commit on top of the coder's 07119e0)

## Live-data verification
`ItemData` unchanged vs the most recent hourly btrfs snapshot
(`/opt/TGW/.snapshots/20260715T0900`) both before and after this review's
own fix: same 55,419-item count, zero diff on the specific item exercised
(`tgw202605040949058`). No live/production write occurred at any point.

## Checked
- Diff scope: `aspect_translation.py` (new, the one named translation
  function per spec item 1), `ebay_draft.py` (now calls it, spec item 2),
  `http_server.py` (saveEbayDraft/prefill/accept_proposals/summary panel,
  spec items 3-5), `api.py` (new `field_set_drift` catalog-verify rule,
  spec item 8), schema doc + invariant C12 cross-reference (spec item 7).
  All in declared scope; only test files added beyond the named list
  (standing carve-out).
- Spec item 1 (translation function): extracted cleanly, `ebay_draft.py`
  calls it instead of inline logic — read the diff, confirmed no
  behavior drift beyond the extraction itself.
- Spec item 3 (Draft Editor aspects form): now targets
  `draft_listing.item_specifics` via the `draft_specifics` accessor, not
  `item_attributes` — this is the fix for Dave's original bug report
  (Metal/Department mismatches). Verified via the coder's `TestClient`
  integration tests (real endpoint, not mocked) and re-confirmed by
  independently re-running them.
- Spec item 4 (accept_proposals): writes to `item_specifics`, matching
  its own banner's contract. **Found a real gap during review**: this
  action lives in `item_action()`, a separate endpoint from `patch_item()`
  where the #1415 `listing_description`-regeneration fix lives — an
  accepted description proposal would silently reintroduce the exact
  stale-push bug #1415 fixed, through this second door. Not caught by
  the coder (outside the packet's literal spec items, which didn't
  mention description at all). Fixed within the bounded fix-attempt cap:
  mirrored the existing regeneration pattern, added a regression test,
  verified adversarially (git-stash the fix, confirm the new test fails
  with `KeyError: listing_description`; restore, confirm it passes).
  Full suite re-run clean after the fix (2290 passed, 1 skipped).
- Spec item 6 (accept_proposals vs. `revision.cmd_revise_apply`
  reconciliation): coder chose NOT to route through `cmd_revise_apply`,
  reasoning that it requires a live `offer_id` and pushes immediately,
  which would break `accept_proposals`' staged two-step contract and
  hard-fail pre-publish items. Read `revision.py`'s `cmd_revise_apply`
  directly to confirm this reasoning — accurate; the two really are
  legitimate distinct consumers of the same `revision_draft.delta`
  shape for two different flows, not an unresolved duplication. Accepted
  as a well-reasoned, disclosed design decision, not a deviation to
  reject.
- Spec item 8 (`field_set_drift` catalog-verify rule): read the
  implementation, confirmed it matches the spec's gating condition
  exactly (only flags when `ebay_offer.offer_id` is present — pre-publish
  drift is normal churn, not a finding). **Live-tested it myself**
  (read-only) against `tgw202605040949058`: correctly flags `Type` as
  drifted (`Lapel Pin` in Set A vs `Brooch` in Set B) — the exact
  original bug from this session's investigation.
- Invariant C12: no violation after the fix (re-verified the static
  detector clean, including fixing the line-number drift my own edit
  caused in the allowlist — a mechanical consequence of inserting lines
  above an existing allowlisted hit, not a new architectural violation).
- Full offline suite: independently re-run twice (once revealing the
  C12 allowlist drift, once clean after fixing it) — final result 2290
  passed, 1 skipped, matches the coder's own claimed baseline plus the
  new regression test.

## Fix-attempt accounting
1 of 2 allowed fix attempts used (the `listing_description` gap +
consequent C12 allowlist line-shift, fixed together as one attempt).
Well within cap — no escalation needed.

## Summary
Cleared after one reviewer-applied fix for a real gap the coder's own
scope didn't cover (accept_proposals' description path). Everything else
matches spec, invariant C12 holds, live evidence independently
re-verified at every step (not taken on manifest trust), no live data
touched. Cleared for stitch.
