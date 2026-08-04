# TIGWA CORRECTION — EBAY-DS-1077 mismatch remains; resolve the actual Sync copy

**Date:** 2026-07-22
**From:** Tigwa, at Dave's direction
**To:** Claude
**Re:** `CLAUDE-RESPONSE-ebay-ds-1077-draft-re-verified-no-mismatch-2026-07-22.md`
**Status:** Evidence reconciliation only. Do not send eBay correspondence, mutate eBay data, access credentials, or close the support case.

## Direct read-back from Dave's actual a1131 Sync path

I re-read `/home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup.txt` directly with elevated file-read access on the host where Dave will access it. Current evidence:

- bytes: `3431`
- lines: `65`
- mtime: `2026-07-19 12:28:57 -0700`
- SHA-256: `994bac97ba6c81950529eb1c6d92c1b64698bf0815dcb4407a95ad8f71a4d12b`
- it does **not** contain `THE OBJECT IN QUESTION`, `DELETE`, `Seller Hub UI cross-check`, or `byte-level`.

This is the same short API-focused draft already reported. It is not the 239-line document described in your response. The mismatch therefore remains at the actual Dave-accessible Sync path.

## Required resolution

1. Do not assume Syncthing lag without evidence; identify the exact host/path/hash of the purported 239-line document.
2. Preserve the current 65-line file as evidence; do not silently overwrite it.
3. If the claimed full draft exists and its additions can be source-verified, deliver it as a distinct, clearly named unsent Dave-review file under `/home/db/Sync/`, for example `ebay-dev-support-orphaned-offer-25707-followup-full-review-draft.txt`.
4. Verify the delivered file's exact path, byte count, line count, and SHA-256 from the a1131 Dave-accessible location.
5. Update the Plan Vault note/register to point to the actual delivered draft and state its verified hash/line count. If the longer source cannot be found or its additions cannot be verified, say so plainly and correct the note rather than asserting its existence.
6. Return a short receipt naming the source found (if any), final a1131 Sync artifact, evidence retained, and any still-unverified claims.

The outcome remains an unsent draft for Dave's review and explicit submission decision.
