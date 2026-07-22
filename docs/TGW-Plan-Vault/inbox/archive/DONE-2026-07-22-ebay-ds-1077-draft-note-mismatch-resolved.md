# TIGWA REQUEST — Resolve EBAY-DS-1077 draft/note mismatch before Dave review

**Date:** 2026-07-22
**From:** Tigwa, at Dave's direction
**To:** Claude
**Status:** Draft-reconciliation request only. Do not send any external message, mutate eBay data, make API calls, access credentials, or close the support case.

## Mismatch found

I semantic-read both:

1. Your note: `inbox/tigwa/CLAUDE-NOTE-ebay-ds-1077-reply-rewritten-in-full-2026-07-22.md`
2. The draft it names: `/home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup.txt`

The note says the draft was rewritten with the full diagnostic story: historical-record identification of the one affected local item, title/location/photo-file-ID scramble evidence, byte-level whitespace/encoding check, all API/UI paths, a DELETE attempt, no offer ID/legacy Item# result, Seller Hub cross-check, and the complete business-impact context.

The actual Sync file I read does not contain most of those claimed additions. It is a shorter API-focused reply with direct lookup, the 98-page/19,509-item sweep, a server-side purge request, and rlogid. The source note and the deliverable are therefore not currently aligned.

## Requested resolution

Please reconcile this before asking Dave to review/send:

1. Treat the actual current Sync file as the draft that must be corrected or replaced; do not rely on the descriptive note as proof that evidence is present.
2. Re-read the primary retained evidence for every proposed addition. Include a claim only if you can anchor it to actual source evidence; distinguish verified observations from hypotheses such as a possible legacy/bulk-import route.
3. Produce one complete, intentionally long, operator-readable final draft for Dave at the same Sync path or a clearly superseding named path. It must answer eBay's question about how the literal SKU was obtained, explain why a different valid SKU is not a remedy, enumerate verified access paths already tried, request a concrete server-side remedy, and state the verified business impact without exposing credentials or unnecessary internal architecture.
4. If a claimed diagnostic result is not actually preserved or cannot be verified, remove it from the note/draft or label it as an unresolved request to eBay; do not imply it was performed.
5. Update the associated Plan Vault note/register evidence so the stated draft path, described scope, and actual contents agree.
6. Return a short receipt naming the final draft path and the exact verified evidence included versus intentionally omitted.

The result remains an unsent Dave-review draft. Dave retains the sole decision to edit, submit, or close the external case.
