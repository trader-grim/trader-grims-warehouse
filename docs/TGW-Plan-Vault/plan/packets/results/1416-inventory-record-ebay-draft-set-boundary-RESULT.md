# Result: 1416 inventory-record-ebay-draft-set-boundary

Status: done
Todo: #1416   PP: PP-LISTEDITOR-001

## Files touched

- `src/tgw/ebay/aspect_translation.py` (new) — the one named Set A → Set B
  translation function, `translate_inventory_to_ebay_draft()`, extracted
  from `workers/ebay_draft.py`'s former inline "Phase 2b" prefill block
  (spec point 1).
- `src/tgw/workers/ebay_draft.py` — Phase 2b now calls the extracted
  function instead of inline logic (spec point 2). No behavior change: the
  extracted function is a verbatim lift of the prior inline loop (same
  matching rule, same SELECTION_ONLY/allowed_values gate, same priority
  order vs `product_lookup`), confirmed by the full offline suite staying
  green and by dedicated new unit tests.
- `src/tgw/http_server.py`:
  - `_apply_patch()` — new handling for `draft_listing.item_specifics`:
    a bare partial `{name: value}` dict nested inside an incoming
    `draft_listing` PATCH is now routed through the sanctioned
    `tgw.ebay.draft_specifics.set_ebay_aspects()` accessor (envelope +
    provenance history preserved), never shallow-merged directly onto the
    envelope — same discipline `item_attributes` already had from #1418,
    extended to the nested Set B field.
  - `saveEbayDraft()` JS — aspects form edits now sent as
    `draft_listing.item_specifics`, not top-level `item_attributes` (spec
    point 3).
  - Aspects-form prefill (`window._DL_PREFILL`) now sourced from
    `draft_specifics.get_ebay_aspects(item)` (Set B), not
    `inventory_record.get_inventory_fields(item)` (Set A) (spec point 3).
  - `accept_proposals` action — accepted `item_specifics` delta now
    written into `draft_listing.item_specifics` via `set_ebay_aspects()`,
    not into `item_attributes` — matches the button's own banner contract
    ("copies proposals into your draft — review then Update Listing to
    push to eBay") (spec point 4).
  - Inventory Record "Item Specifics" summary panel — the
    `{**item_specifics, **item_attributes}` blend removed; now shows Set A
    (`item_attributes`) unblended, with any differing Set B value shown as
    a clearly-labeled dimmed secondary line ("eBay value: X") only when it
    actually differs (spec point 5).
- `src/tgw/api.py` — new `field_set_drift` catalog-verify rule in
  `_verify_item()`: flags items with a live `ebay_offer.offer_id` where
  Set A and Set B disagree on an overlapping key (spec point 8's data-drift
  detector).
- `docs/TGW-Plan-Vault/reference/TGW-Item-JSON-Schema.md` — cross-referenced
  the new translation function under the existing Set A/Set B section
  (spec point 7; the envelope-shape documentation itself already landed
  with #1418).
- `docs/TGW-Plan-Vault/reference/invariants.md` — C12 entry updated: the
  `field_set_drift` detector is now built and cross-referenced (spec point
  8; C12 itself, its static code-detector, and the envelope shape already
  landed with #1418).
- `tests/test_aspect_translation.py` (new) — 6 unit tests for the
  translation function (category matching, SELECTION_ONLY gating,
  free-text passthrough, already-filled skip, empty-value skip, category
  99 short-circuit without calling `get_aspects`).
- `tests/test_invariant_c12_field_set_accessors.py` — allowlist line
  numbers updated for the moved/added legitimate hits (accessor patch
  writes in the new `draft_listing.item_specifics` routing, and
  `revision_draft.delta` reads in `accept_proposals`); both C12 tests pass
  clean (no new violations).
- `tests/test_catalog_verify.py` — 3 new tests for the `field_set_drift`
  rule (flagged when live + drifted, not flagged when no offer_id, not
  flagged when Set A/Set B agree).
- `tests/test_http_server.py` — updated the 3 existing `accept_proposals`
  tests (previously asserting the OLD, buggy `item_attributes` target —
  renamed and rewritten to assert the corrected `draft_listing.
  item_specifics` target, plus an explicit assertion that `item_attributes`
  is untouched by `accept_proposals`); added 4 new tests: Inventory Record
  panel unblended rendering, aspects-form prefill from Set B, and a
  `saveEbayDraft()`-shaped PATCH exercising the new `_apply_patch` accessor
  routing end-to-end through the real HTTP endpoint (including the
  existing auto-push-on-draft_listing-change trigger still firing).

## Live evidence

1. **Full offline suite, zero regressions**: `2290 passed, 1 skipped` (up
   from #1418's `2278 passed, 1 skipped` baseline by exactly the 12 new
   tests added this session: 6 in `test_aspect_translation.py`, 3 in
   `test_catalog_verify.py`, 3 net-new in `test_http_server.py` — the 3
   `accept_proposals` tests were rewritten in place, not added). Run twice
   during the session (after the ebay_draft.py fix and again as the final
   check), thermal checked `NORMAL` before both runs and the earlier
   individually-run test files.
   ```
   PYTHONPATH=<worktree>/src:$PYTHONPATH LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH \
     python3 -m pytest -q
   → 2290 passed, 1 skipped, 1 warning in 150.64s
   ```
   Confirmed testing the worktree's own code, not the shared checkout:
   `python3 -c "import tgw.http_server as h; print(h.__file__)"` resolved
   under `/opt/TGW/var/worktrees/1416-.../src/tgw/http_server.py`.
2. **C12 static detector, clean**: `tests/test_invariant_c12_field_set_accessors.py`
   passes (3/3) after this packet's changes — no new direct-dict-access
   violation introduced by the fix; the allowlist entries were re-verified
   line-by-line against the actual moved code, not just renumbered blindly.
3. **`field_set_drift` detector run against real, live ItemData (dry,
   read-only, no writes)** — spot-checked against `tgw202605040949058`, the
   exact item cited in the packet's own investigation (per Acceptance item
   6's instruction to check current state first, since an earlier same-day
   restore might have already fixed it):
   ```
   FOUND: {'rule': 'field_set_drift', 'sku': 'tgw202605040949058',
     'severity': 'warning', 'detail': 'item_attributes (Set A) and
     draft_listing.item_specifics (Set B) disagree on live item,
     key(s): Type'}
   offer_id: 266061679018
   drift keys: {'Type': ('Lapel Pin', 'Brooch')}
   ```
   Confirms both that the drift is still live (not yet fixed by any
   earlier restore) and that the new detector correctly flags it.
4. **Integration-level (real FastAPI `TestClient`, real HTTP request/response
   cycle through the actual endpoint code, not mocks) verification of the
   UI-facing fixes** — see the 4 new/3 rewritten tests in
   `tests/test_http_server.py`, all passing:
   - `test_saveebaydraft_shaped_patch_routes_item_specifics_through_accessor`:
     PATCHes `draft_listing.item_specifics` exactly as the fixed
     `saveEbayDraft()` JS now does; confirms the write lands as a proper
     Set B envelope with provenance history (`previous_value` correctly
     captured), sibling `draft_listing` fields survive the merge, and the
     existing auto-push-to-`ebay_stage` trigger still fires unchanged.
   - `test_item_detail_aspects_form_prefills_from_set_b_not_set_a`:
     confirms `window._DL_PREFILL` now renders from `draft_listing.
     item_specifics` ("Brooch"), not `item_attributes` ("Lapel Pin").
   - `test_item_detail_inventory_record_panel_shows_set_a_unblended`:
     confirms the summary panel shows Set A's own value plus a clearly-
     labeled "eBay value: X" secondary line only when they differ, and
     never for agreeing keys.
   - `test_accept_proposals_persists_item_specifics_edit` /
     `test_accept_proposals_item_specifics_absent_before`: confirm accepted
     proposals land in `draft_listing.item_specifics` (Set B, envelope +
     history) and explicitly that `item_attributes` is untouched.
5. **Did NOT run a real live eBay Inventory API push test-item cycle**
   against the running `tgw-http:7373` production service — see Deviations
   below for why, and what was done instead as the substitute evidence.

## Deviations from spec

- **Point 6 (reconcile the two `revision_draft` consumers) — design
  decision, NOT routed through `revision.cmd_revise_apply`.** After
  reading `cmd_revise_apply` in full: it requires an existing Inventory
  API `offer_id` (hard-fails otherwise) and performs an immediate live
  GET→compose→PUT with no staging step. Routing `accept_proposals` through
  it would (a) break `accept_proposals`' own stated two-step contract
  ("accept" stages into the draft; a separate "Update Listing" click
  pushes), turning every accept into an immediate live write instead, and
  (b) hard-fail for any item with a pending `revision_draft` but no live
  offer yet — a normal, common pre-publish state `accept_proposals` is
  explicitly meant to support. Instead, `accept_proposals` was fixed to
  write into the SET (`draft_listing.item_specifics`, Set B) that the
  existing staged-push path (`ebay_stage` → `sync.py:_build_offer_bodies`)
  actually reads — closing the cross-set boundary bug (the actual
  reported defect) without collapsing two legitimately different UI flows
  (staged-accept-then-push vs. immediate-apply) into one. Both mechanisms
  remain distinct, intentional consumers of the same `revision_draft.delta`
  shape for two different screens (item page's "Accept All Proposals" vs.
  `/form/revisions`). Full reasoning is inline in `http_server.py`'s
  `accept_proposals` block as a comment, not just here.
- **Point 5 (Inventory Record panel) — minor UX judgment call**: the spec
  said "use your judgment on the minimal correct UI." Implemented as a
  dimmed secondary line under the same row (`eBay value: X`), shown ONLY
  when the eBay-side key exists AND differs from the Set A value — chosen
  over always showing both to reduce visual noise for the common case
  where the two sets agree (the far more common state post-`ebay_draft`
  run).
- **Point 1 (translation function signature) — extended beyond the spec's
  literal `(item_attributes, category_id, cfg)`**: added an optional
  `aspects=` kwarg accepting a pre-fetched aspect-definitions list. Found
  necessary mid-implementation: `ebay_draft.py` already fetches the
  category's aspects once for several other phases in the same job; having
  the translation function silently re-fetch via its own `get_aspects()`
  call broke an existing test (`test_ebay_draft_nonjson_truncation.py`,
  which monkeypatches `ebay_draft_mod.get_aspects` — a different module
  binding than `aspect_translation.get_aspects`) and would have been a
  redundant lookup even where mocking wasn't the issue. `aspects=None`
  still falls back to fetching internally, so the function works
  standalone for any future caller that hasn't already fetched aspects —
  the spec's core contract (given item_attributes + category, return the
  matching item_specifics) is unchanged, this only avoids a needless
  duplicate call when the caller already has the data.
- **Did not perform a real live eBay Inventory API push against a
  throwaway test SKU via the running `tgw-http:7373` service.** The
  running production `tgw-http` service is bound to the SHARED checkout's
  editable install (`/opt/TGW/src/trader-grims-warehouse/src`), not this
  worktree — exercising it live would either (a) test the wrong code
  (shared checkout, not this branch's fix) or (b) require restarting the
  live production `tgw-http.service` with this branch's code, which is a
  real production change outside a single work-packet's authority and
  outside today's stated live-write scope. Instead, verified via: (i) the
  full offline suite, (ii) real `TestClient`-driven HTTP requests through
  the actual FastAPI app + real temp-filesystem item docs (not mocked —
  same mechanism the existing `#1114`/`#1291` regression-test suite in
  this file already uses as its live-verification standard), and (iii) a
  read-only spot-check of the drift detector against real production
  ItemData. Flagging this explicitly rather than silently treating (ii) as
  equivalent to an actual eBay API round-trip — the eBay-side push code
  path itself (`sync.py:_build_offer_bodies` reading `item_specifics`) was
  not touched by this packet and was already covered by #1418's landing,
  so the residual risk here is specifically "does the operator-facing
  PATCH shape reach the field sync.py reads," which the integration tests
  above do exercise correctly.

## Out-of-scope findings filed

None. No adjacent bugs were found outside this packet's declared scope
during the investigation (the packet's own pre-work already surfaced and
scoped the four boundary-violation sites this packet fixes).
