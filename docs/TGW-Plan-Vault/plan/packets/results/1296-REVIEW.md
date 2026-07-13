# Review: 1296 promo-sync-null-href
Status: cleared — stitched in `94dd32d` ("pilot run 2 of new sequence,
2-in-a-row clean"). RECONSTRUCTED RETROACTIVELY 2026-07-13 from
`1296-RESULT.md` evidence + the merge commit; no contemporaneous
REVIEW.md was written (compliance gap — the only one of the 7 missing
reviews that was NOT part of a concurrent batch; run sequentially as the
second half of the cadence rule's 2-in-a-row proof alongside #1287, so
the concurrent-batch theory doesn't fully explain this gap).
Reviewer: Claude (retroactive reconstruction)

Checked (from RESULT.md's own reported evidence): one-line fix applied
exactly as specced — `promo_summary.get("promotionId") or
(promo_summary.get("promotionHref") or "").split("/")[-1]`. Confirmed
testing against the worktree's own module, not the shared checkout. New
`TestPromoSyncNullHref` cases cover exactly the packet's 3 acceptance
scenarios: both id/href null → no AttributeError, entry skipped, no
downstream call; id present → called with the id; id null but href
present → called with the href-derived fallback. Targeted run: 44 passed.
Full offline suite: 2049 passed, 1 skipped. No live/sandbox eBay call
made (packet marked this optional-bonus only; fix is pure parsing logic).
No deviations from spec, no out-of-scope findings.

Run 2 of 2 for this pilot sequence (paired with #1287 per the cadence
rule) — 2-in-a-row clean, sequence graduated to concurrent execution
starting with the next batch.

Stitched.
