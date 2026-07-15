# Packet: ebay_publish dead-letter, "Brand is missing" — verify isolated vs. systemic before fixing

Todo: #1404   PP: PP-DEADLETTER-001   Track: dead-letter triage (batch,
see PP-DEADLETTER-001.md — dispatched alongside 7 other packets this
round)

## Context budget (ALL the model may load)
This packet + item JSON for `tgw202605051925361`
(`ItemData/tgw202605051925361/tgw202605051925361.json`, via the fence,
read-only) + `src/tgw/workers/ebay_publish.py` (whole file) +
`src/tgw/workers/ebay_draft.py`'s Brand-aspect-fill logic (search for how
`"Brand"` gets set — it's one of the aspects the LLM fills, see the
non-JSON packet #1393's sample data, which shows `"Brand"` is a normal
field in the aspect-fill JSON for most items, so a missing Brand here is
either this item's LLM response having `"Brand": null` with no fallback,
or an operator/category-config gap).

## Verified live before this packet was written
- 1 `ebay_publish` dead-letter: `HardFailure('tgw202605051925361: eBay
  rejected publish: A user error has occurred. The item specific
  Brand\xa0is missing.\xa0Add Brand to this listing, enter a valid value,
  and then try again.')` — a single SKU, single occurrence, finished
  2026-07-05.

## Spec
1. Read `tgw202605051925361`'s stored item JSON — check whether `Brand`
   is present, null, or absent from its aspects/draft_listing data.
2. If `Brand` came back `null` from the LLM aspect-fill (a legitimate "I
   don't know" answer) and the item's eBay category requires it: check
   whether `ebay_draft.py`/`ebay_stage.py` has (or should have) a
   required-aspect completeness check *before* queuing to publish, so
   this class of rejection is caught earlier with an actionable operator
   finding instead of failing at the final eBay-side publish step.
3. If this is a one-off (e.g. a genuinely unbranded/generic item where no
   reasonable Brand value exists — "Unbranded" is a valid eBay value for
   many categories, check whether that's a viable fallback for this
   category), the fix may just be: set `Brand: "Unbranded"` (or eBay's
   equivalent per-category accepted value) for this one item and confirm
   whether a required-aspect check is even warranted as a general fix, or
   whether this is rare enough not to be worth one (say so plainly if
   that's your conclusion — a one-off data correction is an acceptable
   outcome, per this packet's title).
4. Do not force a big required-aspects-validation subsystem if the
   evidence for one item doesn't support it — scale the fix to what's
   actually shown.

## Out of scope
- Building a general required-aspects validator for all categories unless
  you find clear evidence (e.g. other items showing the same class of
  gap) that this is systemic, not isolated.
- Any other SKU.

## Dataset
If you set `Brand` for this one item, that's a legitimate operator-style
data correction on a stored item field — fine per the Data Charter (this
is not discarding/overwriting an existing asset, it's filling a genuinely
missing required field for eBay compliance).

## Acceptance (live)
1. Report in the result manifest: is this isolated (1 item, done) or
   systemic (found other items with the same required-aspect gap)?
2. If isolated: item corrected, verified it would now pass eBay's Brand
   requirement (check via `tgw item get` after the fix, or a dry-run
   validation if one exists — don't necessarily need a live eBay publish
   call for this single-item fix, use judgment on whether that's
   warranted vs. just fixing the stored data and letting the normal
   requeue/retry cycle confirm it).
3. If systemic: propose (don't necessarily build, unless small) where a
   required-aspect pre-flight check would go.
4. Run the full offline suite — zero regressions if any code changed.

## Quota/risk
None to low — a single item data correction plus investigation; no bulk
operations or new recurring API calls.
