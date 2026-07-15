# RESPONSE — Field-set boundary and delivery-pipeline documentation review

**Responding to:** `TIGWA-REVIEW-fieldset-boundary-diagram.md`
**Reviewer:** Claude
**Date:** 2026-07-15

## Answers to your review questions

1. **Yes, one artifact does** — the HTML states the Set B accessor
   differently from the actual merged code (this review ran after
   #1418 landed, so "the source" here is the real `src/tgw/ebay/
   draft_specifics.py`, not just the packet spec). Two concrete errors,
   both isolated to the eBay Draft (Set B) box:
   - Module labeled `ebay_draft_specifics` — actual is `tgw.ebay.draft_specifics`.
   - Function labeled `set_ebay_fields` (and implies `get_ebay_fields`)
     — actual functions are `set_ebay_aspects`, `get_ebay_aspects`/
     `get_ebay_aspect`. "Fields" vs "aspects."
   Everything else in the HTML (Set A box, envelope shape, invariant
   statement, execution pipeline) checked out correct.

2. **Yes, in the Markdown companion — unmistakable and correct.** Verified
   every claim against the merged code: envelope shape, both history-array
   paths (including the `draft_listing.item_specifics_history` nesting),
   and the prohibitions section all match exactly. The core invariant is
   stated cleanly.

3. **The Markdown/YAML/Mermaid companion has the right shape, and I'd
   point at *why* the HTML and the Markdown diverged as the useful
   lesson here**: the Markdown deliberately uses generic role
   descriptions ("inventory_record accessor module only," "eBay-draft
   specifics accessor module only") instead of committing to concrete
   function names, and that's exactly what let it dodge the naming
   drift the HTML fell into. Where the Markdown *does* name a concrete
   function (`translate_inventory_to_ebay_draft`,
   `diff_ebay_draft_to_inventory`), it correctly quotes those verbatim
   from the #1416/#1417 packet specs rather than inventing them — good
   sourcing discipline, worth keeping as the pattern for future
   companions like this.

4. `reference/` is the right durable home. I'd hold off linking either
   from the master plan/`CLAUDE.md` until the HTML's two labels are
   fixed (already filed as todo #1422, delegated to you) — otherwise a
   future reader hits a linked, "authoritative-looking" doc with wrong
   names in it.

## What's already in motion

- Todo #1422 (delegated to you): fix the two HTML labels above.
- Todo #1421 (yours, already closed): the Markdown companion — no
  further action needed there, it's correct as delivered.

Nice work on the companion doc's approach — the abstraction choice there
is the right lesson for future diagrams: name concrete functions only
when quoting a real source, describe roles generically otherwise.
