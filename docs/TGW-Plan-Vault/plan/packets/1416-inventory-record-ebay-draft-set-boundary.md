# Packet: Inventory Record vs eBay Draft — set boundary fix (not a field-by-field patch)

Todo: #1416   PP: PP-LISTEDITOR-001   Track: bugfix (single packet, high care)

**Sequencing (added 2026-07-15, after this packet was first written):
depends on #1418 (field-set schema foundation) landing first.** #1418
establishes the `_set`-tagged envelope + named accessor modules this
packet's translation function and edit-path fixes should be built on top
of, so this packet doesn't touch the bare-dict shape and then get
re-touched for the envelope migration. Read #1418 before implementing;
every reference below to `item_attributes`/`item_specifics` as bare
dicts should be understood as "via the #1418 accessor modules" once that
packet has landed.

## Context budget (ALL the model may load)
This packet + `src/tgw/http_server.py` sections: `patch_item()` (~687-830),
`_render_item_detail_html`'s Inventory Record block (~5646-5710), the
aspects summary/editor rendering (~4940-4970, ~5570-5600, ~5920-5980),
`accept_proposals` action (~1420-1455) + `_CATEGORY_CONTEXT_IIFE` JS
(~2582) + `src/tgw/workers/ebay_draft.py` lines ~420-480 (item_attributes
prefill) + `src/tgw/ebay/sync.py` lines ~440-460 (`_build_offer_bodies`
aspects assembly) + `src/tgw/revision.py` (whole file, ~350 lines) +
`reference/TGW-Item-JSON-Schema.md` + `reference/invariants.md` C9/C10/C11
sections (same family, read for house style).

## Verified live before this packet was written
Two rounds of live investigation on SKU `tgw202605040949058` (offer
266061679018, listing 227431727552) today, 2026-07-15, plus a full
codebase audit. Key facts, all confirmed against running code/live data,
not inferred:

1. **This item's description-push bug (todo #1415, already fixed and
   merged)** was a *within-set* staleness bug: `draft_listing` is one
   set, and `description`/`listing_description` are two views of the
   same fact inside it that must be regenerated together. Fixed by
   regenerating `listing_description` whenever `description` changes,
   inside the same PATCH. This packet does NOT touch that fix — it's
   the model for what "treat it as a set" looks like, cited here for
   contrast with the bug below, which is a *cross-set* problem, not a
   within-set one.

2. **The actual recurring bug (Dave: "over and over and over again")**
   is a boundary violation between two DIFFERENT sets that were never
   supposed to be reconciled key-by-key:
   - **Set A — Inventory Record**: `title`, `description`, `condition`,
     `item_attributes` (a dict of aspect-like facts: Type, Brand,
     Metal, Department, etc.) — universal, marketplace-agnostic, meant
     to translate across eBay and any future marketplace. `item_attributes`
     is NOT documented in `TGW-Item-JSON-Schema.md` at all (confirmed:
     grep returns zero hits) — an undocumented dict four separate code
     paths independently reinvented uses for.
   - **Set B — eBay Draft**: `draft_listing.*` as a whole, including
     `draft_listing.item_specifics` — the eBay-specific, category-mapped
     aspect values. `sync.py:441-443`'s `_build_offer_bodies` is the
     ONLY code path that reads aspects for the actual eBay Inventory API
     push (`product.aspects`), and it reads exclusively from
     `draft_listing.item_specifics`. Confirmed live: eBay's `getInventoryItem`
     for this offer returns 21/21 aspects matching `item_specifics`
     byte-for-byte, while `item_attributes` disagrees on multiple keys
     (`Type: "Lapel Pin"` vs eBay's actual `"Brooch"`, among others).

3. **Four independent code paths touch `item_attributes`, three of them
   incorrectly treat it as if it were on the path to eBay:**
   - `workers/ai_identify.py:322-326` — writes it. Correct in isolation
     (this is Set A's legitimate writer), no eBay-push claim.
   - `workers/ebay_draft.py:456` — reads it as a low-priority *prefill
     source* when building `item_specifics` from scratch. This is the
     ONE legitimate Set A → Set B translation point today, but it's
     partial/ad hoc (some keys, `allowed_values`-gated) rather than an
     explicit, complete, single translation function.
   - `http_server.py:5979` (`saveEbayDraft()`, the eBay Draft Editor's
     aspects-editing form, `#aspects-form`/`dl-asp-*` inputs) — reads
     its prefill from `item_attributes` (`_aspects_prefill_json`,
     `http_server.py:4949`, labeled in a comment "operator edits —
     highest priority") and PATCHes edits back into `item_attributes`.
     **Operating on Set B's own editing surface, but writing into Set
     A.** The edit never reaches `draft_listing.item_specifics`, so it
     never reaches eBay. Confirmed live: two ebay_stage jobs fired and
     "succeeded" today while pushing unchanged (wrong) content, because
     nothing in the pushed set had actually changed.
   - `http_server.py:1434-1436` (`accept_proposals` action, the item
     page's "Accept All Proposals" button) — copies
     `revision_draft.delta.item_specifics` into **`item_attributes`**,
     not `draft_listing.item_specifics`. The button's OWN banner text
     (`http_server.py:5362`) says *"Accept copies proposals into your
     draft — review then Update Listing to push to eBay"* — the code
     does not do what its own UI copy claims. Same bug shape, a fourth
     independent occurrence, contradicts its own stated contract.
   - `http_server.py:5697,5709` (Inventory Record's "Item Specifics"
     summary panel) — renders `{**item_specifics, **item_attributes}`
     (Set B spread first, Set A spread last, so Set A silently wins on
     any overlapping key) inside a panel explicitly labeled "Canonical
     TGW data — never overwritten by marketplace sync." This blends two
     sets' individual keys into one display with no way to tell, at a
     glance, which set any given visible value actually came from.

4. **A fifth, competing mechanism** (`revision_draft`, consumed by both
   `accept_proposals` above AND the separate `/form/revisions` review
   screen → `revision.cmd_revise_apply`, which correctly does a live
   GET→PUT bypassing `draft_listing`/`item_attributes` entirely) means
   the SAME accepted-proposal data has two live consumers that disagree
   about which set it belongs to, keyed off nothing but which button the
   operator happens to click.

5. `docs/TGW-Plan-Vault/reference/invariants.md` has no entry covering
   this field pair or this bug class (grep confirms zero matches for
   `item_attributes`/`item_specifics`) — Prime Directive 5 was never
   applied here despite Dave having flagged this exact confusion
   repeatedly before today.

## Dave's framing (binding constraint on the fix, not a suggestion)
"The problem is you are considering keys individually. As long as you
keep that up we will have this issue. They are sets of data. If you
don't look at it that way you will keep mixing them up." Concretely:
**no code path may read or write a single key of Set A or Set B in
isolation as part of a cross-set operation.** Any operation that moves
data between Set A and Set B must go through one explicit, complete,
named translation function — never a per-key merge, prefill fallback,
or `{**a, **b}` spread performed locally in a display or save handler.
Any operation that edits within a single set must write that whole set
(or the relevant whole sub-document) so the set stays internally
self-consistent — the description/listing_description fix (todo #1415)
is the reference example of "edit within one set, done right."

## Spec

1. **Define and name the single translation function.**
   `translate_inventory_to_ebay_draft(item_attributes: dict, category_id: str,
   cfg) -> dict` (exact name/location at implementer's judgment, propose
   `src/tgw/ebay/aspect_translation.py` or fold into `ebay_draft.py` if
   that reads cleaner — flag your choice in the result manifest). Given
   the full universal `item_attributes` set (Set A) and a target eBay
   category, return the full `item_specifics` dict (Set B) it maps to,
   respecting that category's allowed-values/required-aspects (reuse
   existing category-context lookup, do not reinvent). This function
   already exists in spirit inside `ebay_draft.py:420-480` — extract and
   name it explicitly rather than leaving it as inline prefill logic, so
   every future caller has exactly one place to call, not a temptation
   to reinvent a partial version.

2. **`ebay_draft.py`'s own build calls the named translation function**
   in place of its current inline prefill logic. No behavior change to
   its output beyond what's inherent to extracting the function (if you
   find a behavior difference while extracting, that's a real
   pre-existing bug in the inline version — flag it in the manifest,
   don't silently "fix" it beyond making it match its own prior
   behavior, unless directed otherwise).

3. **eBay Draft Editor's aspects form (`#aspects-form`, `saveEbayDraft()`)
   stops reading/writing `item_attributes`.** It is Set B's own editing
   surface — it must prefill from and PATCH directly into
   `draft_listing.item_specifics`, exactly like every other Draft Editor
   field (title, price, condition_enum, etc. already do this correctly
   — match that existing pattern). `_DRAFT_LISTING_FIELDS`'s existing
   auto-push-on-change logic (`http_server.py:807`) then fires
   naturally, same as it does for description/price today — no new
   trigger-set changes needed, `item_specifics` edits become a normal
   `draft_listing` field change.

4. **`accept_proposals` writes the accepted delta into
   `draft_listing.item_specifics`**, not `item_attributes` — matching
   its own banner's stated contract ("copies proposals into your draft
   — review then Update Listing to push to eBay"). This makes its
   behavior consistent with point 3 (both write Set B, both then push
   via the same existing "Update Listing" mechanism) instead of being a
   fourth independent half-implementation.

5. **Inventory Record's "Item Specifics" summary panel shows Set A
   only** (`item_attributes`, unmerged) — remove the `{**item_specifics,
   **item_attributes}` blend at `http_server.py:5697/5709`. If it's
   useful to show the eBay-side value for comparison, do so as a
   clearly-separate, clearly-labeled secondary column/line (e.g. "eBay
   value: X" in dimmed text) — never blended into the same key so a
   viewer can't tell which set they're looking at. Use your judgment on
   the minimal correct UI for this; the hard requirement is that no
   single displayed value may be ambiguous about which set it came from.

6. **Reconcile the two `revision_draft` consumers (point 4 above +
   `/form/revisions`)** so they agree. Given `/form/revisions` →
   `cmd_revise_apply` is the one path that's already correct
   end-to-end (live GET→PUT, no `draft_listing`/`item_attributes`
   involvement), the simplest correct reconciliation is likely: make
   `accept_proposals` call the same underlying apply logic
   `cmd_revise_apply` uses, rather than maintaining a second,
   independent (and currently broken) implementation. If you judge a
   different reconciliation is cleaner, state your reasoning in the
   manifest — this is a design call worth flagging, not silently
   picking.

7. **Add `item_attributes` to `TGW-Item-JSON-Schema.md`** — document it
   as Set A's aspect dict, explicitly note its relationship to
   `draft_listing.item_specifics` (Set B) and the one-way translation
   function from point 1. Cross-reference the new invariant (point 8).

8. **Add a new invariant to `reference/invariants.md`** (next available
   ID in the C-series — C12 as of this writing, but re-check for
   collisions since other work may land first) codifying Dave's rule
   from this packet: field-sets are read/written as wholes; cross-set
   data moves through exactly one named translation function, never a
   per-key merge/prefill/spread. Cite this packet, todo #1416, and the
   four independent occurrences found in the investigation as the "why."
   Add a `catalog-verify` detector: flag items where `item_attributes`
   and `draft_listing.item_specifics` disagree on an overlapping key
   AND the item has a live `ebay_offer.offer_id` (i.e., a drift that
   matters because it's live) — this is the "regularly check and
   repair" half of the invariant, matching the C11 precedent's shape.

9. Run the full offline suite — zero regressions.

## Out of scope
- Any change to the description/listing_description fix (todo #1415) —
  already correct, cited only as a contrast example.
- Building a UI for multi-marketplace support itself — this packet only
  fixes the boundary/translation mechanism so it's *ready* for that,
  per Dave's stated intent; no new marketplace integration.
- `ebay_draft`'s AI extraction/vision logic itself (how it decides
  values) — only how it's structured as a named function and what it's
  allowed to read/write.
- Any other unrelated `draft_listing` field.
- Live production pushes as part of implementation/testing — see
  Acceptance below for the required test-item discipline.

## Dataset
No data loss. This changes which field is authoritative for aspect
writes going forward and adds documentation/an invariant; it does not
delete or overwrite historical `item_attributes` data — if `ebay_draft`
is re-run or the translation function is invoked, `item_specifics` gets
regenerated (already true today), `item_attributes` itself is untouched
except where points 3/4 change what future operator edits write to.

## Acceptance (live)
1. Diff shown for all of: the new/extracted translation function,
   `saveEbayDraft()`, `accept_proposals`, the Inventory Record summary
   panel, `TGW-Item-JSON-Schema.md`, `invariants.md`.
2. **Do not use `tgw202605040949058` or any other real live listing for
   the live test** (per today's incident — a live listing got test text
   pushed to it and had to be manually restored). Use the safe
   test-item technique: "Everything Else" category, absurd price,
   clearly-marked test SKU, OR a live item with `ebay_offer.offer_id`
   unset (never actually published) so nothing reaches a real buyer-
   visible listing.
3. Live test: edit an aspect via the (now-fixed) eBay Draft Editor
   aspects form on the test item, confirm `draft_listing.item_specifics`
   changes, confirm `ebay_stage` auto-fires (if the test item has an
   offer_id) and the pushed value matches — OR, if no live offer, confirm
   the field is staged correctly and would push on next stage.
4. Live test: exercise `accept_proposals` on an item with a pending
   `revision_draft`, confirm the accepted values land in
   `draft_listing.item_specifics` and reach eBay via the existing
   apply/push path — or via whatever reconciliation point 6 settled on.
5. Confirm Inventory Record's summary panel shows only `item_attributes`
   values, no blended/ambiguous key.
6. New `catalog-verify` detector shown running against real data (dry
   run is fine — do not bulk-repair anything, this packet just needs
   the detector to exist and correctly flag known-drifted items, e.g.
   spot-check against `tgw202605040949058` which should show a real
   drift right now for at least one key... *unless* today's earlier
   restore already fixed all of them — check current state first before
   assuming this item is still a good detector test case).
7. Full offline suite: zero regressions.

## Quota/risk
Low-moderate. No bulk eBay operations. One or two real eBay pushes for
the live test, against a throwaway/never-published test item only. The
main risk is scope: this touches 4+ files across UI, worker, and sync
code — keep each change minimal and traceable to a specific numbered
spec item; if anything requires touching a file/function not named
above, that's worth flagging rather than quietly expanding scope.
