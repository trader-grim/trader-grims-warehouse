# DONE #1319 — enforce title length at generation time, not just flag it

**Trigger:** Dave flagged a third dead-lettered item (`tgw202605051752520`,
title 81 chars, same eBay rejection as #1318's `...913468`) and stated the
broader principle: "regardless of error we should be pushing the listing
toward eBay compatible through our entire process."

**Root cause:** `src/tgw/seo/title.py::enhance_title()` — the function
`ebay_draft.py` calls to set the final `draft['title']` — only truncated an
oversized title in the narrow case where it was itself injecting a brand and
the result overflowed. If the AI-generated title was already >80 chars with
no brand to inject (or the MPN-append step pushed it over), the function just
appended a `title_too_long` *flag* and returned the oversized title
unchanged. Nothing downstream enforced that flag, so the oversized title
passed through `ebay_draft` → `ebay_upload` cleanly and only failed at
`ebay_stage`'s actual eBay API call — a full pipeline round-trip and a
dead-lettered job later, for something that was knowable at draft-generation
time. All three items dead-lettered this exact way (`tgw202605051752520`,
`tgw202605051913468`, and historically `tgw202605051936445` per its earlier
07-02 dead-letter, though that one's current block is unrelated/price-null).

**Fix:** `enhance_title()` now hard-truncates to `_MAX_TITLE` (80) whenever
the title exceeds it, preferring a whole-word boundary (falls back to a
mid-word cut only for one long unbroken token that would otherwise eat most
of the budget). Replaced the now-unreachable `title_too_long` flag with
`title_truncated`, which fires exactly when this path runs — more useful
than a flag documenting a state that can no longer exist.

**Left alone deliberately:** `listing_quality.py`'s own independent
`title_too_long` scoring flag — separate consumer, separate purpose (quality
score display), still a legitimate defense-in-depth layer for any future path
that sets a title outside `enhance_title()`. Not touched.

**Tests:** `tests/test_seo_title.py` (new file, 4 tests) — oversized-with-no-brand
truncation, MPN-append-never-exceeds-cap, word-boundary preference, and
untouched-when-already-valid. `python3 -m pytest tests/test_seo_title.py
tests/ -k "title or ebay_draft or listing_quality"` — 57 passed, 1 skipped, no
regressions.

**Verified live:** ran the actual function against all 3 real oversized
titles — all truncate cleanly to <=80 chars on a word boundary. Restarted
`tgw-worker@ebay_draft.service` to pick up the change.

**Not done:** did not re-run `ai_identify`/`ebay_draft` on the three affected
items to regenerate their drafts with the fixed title, and did not re-trigger
`ebay_stage`/`ebay_publish` — those are real pipeline/eBay actions, left for
the operator via the now-working Save button (#1318) or a bulk requeue if
Dave wants one.

**Related, not done:** Dave's broader point (the planning-agenda item added
today, section 3) is bigger than this one field — audit what other
eBay-enforced limits aren't locally pre-validated (description length,
item-specifics value length/count, price bounds, image count/dimensions,
category-specific required-field formats). This fix is one instance of that
pattern, not the whole answer.
