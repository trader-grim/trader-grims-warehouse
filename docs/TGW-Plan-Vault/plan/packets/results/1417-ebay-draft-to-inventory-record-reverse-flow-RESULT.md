# Result: 1417 ebay-draft-to-inventory-record-reverse-flow

Status: done
Todo: #1417   PP: PP-LISTEDITOR-001

## Files touched

- `src/tgw/ebay/inventory_diff.py` (new) — the reverse-flow diff engine
  and gated apply function (spec points 1 and 4):
  - `diff_ebay_draft_to_inventory(item) -> list[FieldDiff]` — pure,
    compares Set B (`draft_listing.item_specifics`) against Set A
    (`item_attributes`) key-by-key via the sanctioned accessors. Keys
    present in Set B but absent from Set A are a diff too
    (`inventory_value=None`); keys present only in Set A are excluded.
    `source` is read from the most recent Set B history entry touching
    that key (falls back to `'ebay_draft'`, the only current Set B
    writer, if no history exists); `detected_at` comes from that same
    history entry's `ts` (or the Set B envelope's `updated_at` as a
    fallback, or `None` for a legacy item with neither — never a
    fabricated timestamp, Prime Directive 1).
  - `apply_inventory_diff(item, keys, *, applied_by="operator") ->
    {item_attributes, item_attributes_history, applied_keys}` — pure,
    built on `tgw.inventory_record.set_inventory_fields`. Re-diffs LIVE
    against `item` rather than trusting caller-supplied values, so a
    requested key that's no longer an active diff at call time is a
    silent no-op. Groups requested keys by their diff's `source` (since
    different keys can have different provenance) and calls
    `set_inventory_fields` once per source group so each history entry
    keeps an accurate `source`; each newly-appended history entry is then
    annotated with the diff's own `detected_at` (the extra provenance
    field spec point 4 asks for beyond `set_inventory_fields`'s existing
    `ts`/`source`/`applied_by`/`previous_value`).
- `src/tgw/ebay/draft_specifics.py` — two small additions to the
  sanctioned Set B accessor module (kept the "one sanctioned access
  point" discipline extended to provenance, rather than reading
  `item_specifics_history`/`item_specifics.updated_at` ad hoc from the
  new diff module): `get_ebay_aspects_history(item)` and
  `get_ebay_aspects_updated_at(item)`.
- `src/tgw/http_server.py`:
  - `InventoryDiffApplyBody` pydantic model (`{keys: List[str]}`).
  - `GET /api/items/{sku}/inventory-diff` (spec point 2) — read-only,
    returns the live diff list, never mutates anything.
  - `POST /api/items/{sku}/inventory-diff/apply` (spec point 4) — writes
    ONLY the checked subset into `item_attributes`, via
    `apply_inventory_diff()` then `_apply_patch()` (the returned
    envelope is already a full Set A envelope, so `_apply_patch`'s
    existing `is_envelope()` branch does a correct plain replace — no
    generic PATCH passthrough of partial fields).
  - New UI panel (`#inv-diff-panel`, "eBay → Inventory Record sync")
    inside the Inventory Record section, distinctly labeled and visually
    separate from the existing "Pipeline proposed changes" banner (spec
    point 3) — own fetch/render/apply JS (`loadInventoryDiff()`,
    `applyInventoryDiff()`), no shared function or action name with
    `acceptProposals()`/`dismissProposals()`. Every row default-checked
    per Dave's explicit requirement; loaded via
    `GET /api/items/{sku}/inventory-diff` on page load (`DOMContentLoaded`
    hook added to the existing `_CATEGORY_CONTEXT_IIFE` listener).
- `src/tgw/api.py` — new catalog-verify rule
  `inventory_diff_unresolved_stale` in `_verify_item()` (spec point 7 /
  invariant C13): flags an item where `diff_ebay_draft_to_inventory()`
  finds a key whose `detected_at` is 30+ days old. Deliberately NOT gated
  on a live `ebay_offer.offer_id` (unlike `field_set_drift`) — see
  Deviations below.
- `docs/TGW-Plan-Vault/reference/invariants.md` — new C13 entry: Set A
  writes from the reverse flow are gated/provenance-recorded/
  operator-reviewed, never a silent auto-promotion; documents the
  code/UI/data enforcement layers and the idempotency/sticky-skip design
  decision (spec point 5).
- `docs/TGW-Plan-Vault/reference/TGW-Item-JSON-Schema.md` — updated the
  "Set A → Set B translation" section (previously said "#1417, not yet
  built") to document the landed reverse flow.
- `tests/test_inventory_diff.py` (new) — 15 unit tests for the diff
  engine and apply function: differing/agreeing/Set-B-only/Set-A-only
  keys, source/detected_at derivation from history vs fallback vs
  missing, purity (no mutation), checked-subset-only writes, provenance
  recording, idempotent no-op on a resolved key, re-surfacing of an
  unapplied diff, ignoring a requested key that was never a diff, and
  correct per-source grouping when two keys have different provenance.
- `tests/test_catalog_verify.py` — 4 new tests for
  `inventory_diff_unresolved_stale`: flagged past the 30-day threshold,
  not flagged when recent, not flagged when no timestamp is available
  (legacy item), not flagged once Set A/Set B agree.
- `tests/test_http_server.py` — added `inventory_record` to the test
  file's imports; 9 new integration tests using the real FastAPI
  `TestClient` against real temp-filesystem item docs (not mocks): GET
  diff surfacing a real mismatch (including the Set-B-only "new fact"
  case), GET requires auth, GET 404s on unknown SKU, GET is read-only
  (byte-for-byte file unchanged), POST apply writes only the checked
  keys with full provenance and leaves the unchecked key open, POST
  apply never touches `draft_listing`/`revision_draft` (proves no shared
  write path with `accept_proposals` — spec point 6), POST apply is an
  idempotent no-op on an already-resolved key, POST apply requires auth,
  and the item-detail page renders both panels distinctly labeled with
  no shared button/action name (acceptance item 3).
- `tests/test_invariant_c12_field_set_accessors.py` — allowlist updated:
  renumbered the 7 existing entries (all shifted by this packet's new
  code) and added 3 new entries — the 2 accessor-output-forwarding hits
  inside `inventory_diff.py`'s `apply_inventory_diff()`, and the new
  `_apply_patch(...)` call site inside the new
  `apply_inventory_diff_endpoint()` in `http_server.py`. Both C12 tests
  pass clean (no new unreviewed violation).

## Live evidence

1. **Diff engine, unit-tested (`tests/test_inventory_diff.py`, 15/15
   passing)** — see above.
2. **Integration-level (real FastAPI `TestClient`, real HTTP
   request/response cycle through the actual endpoint code)**:
   `tests/test_http_server.py -k "inventory_diff or inv_diff_panel"` →
   `9 passed`. Full `tests/test_http_server.py` → `283 passed`.
3. **Full offline suite, zero regressions**:
   ```
   PYTHONPATH=<worktree>/src:$PYTHONPATH LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH \
     python3 -m pytest -q
   → 2318 passed, 1 skipped, 1 warning in 155.74s
   ```
   Up from #1416's `2290 passed, 1 skipped` baseline by exactly the 28
   new tests this session (15 + 4 + 9). Thermal checked `NORMAL` before
   this run and before each individually-run test file throughout the
   session. Confirmed testing the worktree's own code, not the shared
   checkout: `tgw.ebay.inventory_diff.__file__` and `tgw.api.__file__`
   both resolved under
   `/opt/TGW/var/worktrees/1417-.../src/tgw/...` when run with the
   `sudo -u tgw env LD_LIBRARY_PATH=... PYTHONPATH=<worktree>/src`
   override (both for pytest and for the live-item checks below).
4. **Live acceptance test on a real, throwaway ItemData SKU** (created
   and fully deleted within this session — `tgw20260715094902010`,
   `status: deleted`/`#STATUS: deleted` from creation, title prefixed
   "THROWAWAY TEST ITEM", `ebay_category_id: "99"` = the non-leaf
   catch-all, no `ebay_offer`/`offer_id` ever set — never a real,
   published, or even publishable listing; same discipline as #1416's
   test-item handling, satisfying this packet's "no real live listings"
   constraint):
   - Seeded `item_attributes={Type: Lapel Pin, Brand: Unbranded}` vs
     `draft_listing.item_specifics={Type: Brooch, Brand: Unbranded,
     Metal: Silver}`, with `item_specifics_history` timestamped
     2026-06-01 (45 days before this session's date).
   - `diff_ebay_draft_to_inventory()` run directly against the real disk
     file (as `tgw` user, worktree `PYTHONPATH`) correctly surfaced
     `{Type: Lapel Pin→Brooch, Metal: None→Silver}` and correctly
     excluded `Brand` (agreeing) — confirms acceptance item 2's "diff
     endpoint surfaces it correctly."
   - `_verify_item()` (catalog-verify, dry-run/read-only, no bulk
     fixes — acceptance item 4) correctly flagged the item:
     ```
     FOUND: {'rule': 'inventory_diff_unresolved_stale', 'sku':
       'tgw20260715094902010', 'severity': 'warning', 'detail': 'eBay
       draft and inventory record have disagreed on key(s) Metal, Type
       for 30+ days with no operator review (GET
       /api/items/tgw20260715094902010/inventory-diff)'}
     ```
     `field_set_drift` (C12's detector) correctly did NOT fire (no live
     `ebay_offer.offer_id`) — confirms C13 is deliberately un-gated by
     live status while C12's detector remains gated, as designed.
   - Simulated the operator unchecking "Metal" in the UI: called
     `apply_inventory_diff(doc, ["Type"])` and wrote the result back to
     the real file. Confirmed: `applied_keys == ['Type']`; Set A now
     `{Type: Brooch, Brand: Unbranded}` (Metal NOT added); new history
     entry has `source=ebay_draft`, `applied_by=operator`,
     `detected_at=2026-06-01T00:00:05+00:00`, `previous_value=Lapel Pin`.
     Re-loaded the file fresh from disk and re-ran the diff engine:
     `remaining diffs == [{key: Metal, ...}]` — the unchecked field still
     shows as an open diff, nothing dismissed (acceptance item 2's full
     checklist, including the both-directions apply→confirm→re-diff
     verification).
   - Test SKU fully deleted (`rm -rf`) at the end of the session —
     confirmed no trace remains in production `ItemData`.
5. **Forward/reverse isolation (acceptance item 3)**:
   `test_item_detail_page_renders_inv_diff_panel_container` confirms both
   panels render on the same test item with distinct labels
   ("eBay → Inventory Record sync" vs "Pipeline proposed changes") and
   distinct buttons/functions (`applyInventoryDiff()`/"Apply Checked to
   Inventory Record" vs `acceptProposals()`/"Accept All Proposals"), and
   `test_inventory_diff_apply_does_not_touch_draft_listing_or_revision_draft`
   confirms the reverse apply endpoint never touches `draft_listing` or
   `revision_draft` (no shared write path with `accept_proposals`).
6. **Did NOT exercise a real live eBay Inventory API round-trip via the
   running `tgw-http:7373` production service** — same reasoning as
   #1416 (the running service is bound to the shared checkout's editable
   install, not this worktree; restarting it with this branch's code is
   outside a single packet's authority). Not applicable here regardless:
   this packet's spec explicitly notes "No eBay API calls beyond what
   already happens (this reads already-local `draft_listing.
   item_specifics`)" — there is no eBay-side round-trip for this feature
   to exercise; the residual risk is entirely local JSON I/O + HTTP
   surface, both covered above.

## Deviations from spec

- **Spec point 5 (sticky-skip vs re-surface) — confirmed the packet's own
  suggested default, not silently assumed.** Implemented "no stored
  dismissed state — an unapplied/unchecked diff reappears on the next
  `GET /inventory-diff` call because Set A/Set B still genuinely
  disagree." Chosen because: (a) it's the packet spec's own stated
  reasoning for why no stored state is needed ("which is correct"); (b) a
  sticky-skip would require a NEW persisted per-key state (a
  `dismissed_diff_keys` list or similar) with no requirement in this
  packet actually driving it — C12/C13 are both working to keep the
  two field-sets' bookkeeping minimal, and adding unrequested state
  cuts against that; (c) it stays symmetric with how `field_set_drift`/
  `inventory_diff_unresolved_stale` already work (dry re-scan every
  time, no stored ack). Documented explicitly in `invariants.md` C13
  ("Idempotency / re-diffing" section) as a confirmed design choice, per
  the packet's own instruction to "confirm this reasoning in the
  manifest rather than assuming." If Dave wants sticky-skip later, it's
  a small additive change on top of this, not a redesign.
- **Spec point 7's C13 detector is NOT gated on a live
  `ebay_offer.offer_id`**, unlike `field_set_drift` (C12's detector).
  This is a deliberate, flagged choice, not an oversight: the packet
  spec doesn't state the "only live" restriction for this detector (it
  does for C12's), and Dave's design intent for this whole packet
  ("gated automatic update... operator can uncheck or skip") reads as
  about routine review cadence for the universal inventory record, not
  specifically live-listing correctness — a pending/never-published
  draft's eBay-discovered value can sit unreviewed just as long as a
  live item's. Worth confirming with Dave alongside the 30-day threshold
  itself (see next point) since both are "your call, flag it" defaults.
- **The 30-day staleness threshold (spec point 7's own proposed default)
  is implemented as literally proposed**, flagged (per the spec's own
  instruction) as a default worth confirming with Dave rather than
  silently treating it as settled — same treatment #1416 gave its own
  proposed defaults.
- **Module location**: placed the diff engine in a new sibling module
  `src/tgw/ebay/inventory_diff.py` rather than adding it to
  `aspect_translation.py` (the packet's spec text offered both as
  options: "propose alongside #1416's translation function, e.g.
  `src/tgw/ebay/aspect_translation.py` or a sibling module"). Chose the
  sibling-module option to keep the FORWARD (`aspect_translation.py`)
  and REVERSE (`inventory_diff.py`) code paths in physically separate
  files, reinforcing spec point 6's "keep the two proposal systems'
  code paths clearly separate" at the file-layout level, not just the
  function-boundary level.
- **`draft_specifics.py` extended with two new accessor functions**
  (`get_ebay_aspects_history`, `get_ebay_aspects_updated_at`) beyond
  what #1418/#1416 originally shipped. Not explicitly speced, but a
  small, in-spirit extension: the diff engine needs each key's most
  recent Set B provenance to attribute a proposed Set A write correctly,
  and reading `item_specifics_history`/`item_specifics.updated_at` ad
  hoc from a caller outside the accessor module would have been exactly
  the kind of scattered raw-dict access C12 exists to prevent — so the
  read was added to the sanctioned accessor module instead. Both
  functions are pure reads with no behavior change to the existing
  accessor surface.

## Out-of-scope findings filed

None. No adjacent bugs were found outside this packet's declared scope
during the investigation or live verification. The packet's own
Out-of-scope list (multi-marketplace support, auto-promotion/
confidence-threshold logic, bulk backfill across the 55k catalog) was
respected — no code for any of the three was written; `source` already
generalizes to a future marketplace name without any marketplace-specific
logic added, and the mechanism was proven on exactly one throwaway test
item, never swept across the catalog.
