# Packet: ebay_upload dead-letters on oversized photo dimensions (no pre-flight resize)

Todo: #1398   PP: PP-DEADLETTER-001   Track: dead-letter triage (batch, see
PP-DEADLETTER-001.md — dispatched alongside 7 other packets this round)

## Context budget (ALL the model may load)
This packet + `src/tgw/ebay/upload.py` (whole file, 100 lines — the
`upload_photo()` function that posts raw bytes with zero pre-flight
checks) + `src/tgw/thumbnail.py` (existing PIL usage pattern to follow,
this codebase already uses Pillow in several places — check
`fingerprint.py`/`image_hash.py`/`ai_identify.py` too for the established
`from PIL import Image` idiom) + `src/tgw/workers/ebay_upload.py` (whole
file — the caller/retry wrapper). Nothing else.

## Verified live before this packet was written
- 10 `ebay_upload` dead-letters:
  `RuntimeError('tgw<SKU>: 0/N new photos uploaded, N failed:
  UploadSiteHostedPictures failed (Failure): File dimension limit exceeds
  15000 pixels.')`
- `upload_photo()` in `src/tgw/ebay/upload.py:39-99` reads the photo file
  raw (`photo_path.read_bytes()`, line 71) and POSTs it directly to
  eBay's `UploadSiteHostedPictures` endpoint with **no dimension check or
  resize step anywhere in this function or its caller** — the failure is
  eBay's own Trading API rejecting an oversized image (>15000px on a
  side), not a bug in our HTTP handling.
- Pillow is already a project dependency, used the same way (lazy
  `from PIL import Image` inside the function, `with Image.open(path) as
  img:`) in `alt_text.py`, `fingerprint.py`, `ai_identify.py`,
  `image_hash.py`, `thumbnail.py` — follow that existing idiom, don't
  introduce a new image-library pattern.

## Spec
1. In `upload_photo()` (or a small helper it calls), add a pre-flight
   dimension check using Pillow: if either dimension exceeds eBay's limit
   (15000px, confirmed by the live error text — verify eBay's documented
   actual limit isn't slightly different before hardcoding), downscale the
   image to fit within the limit before upload, preserving aspect ratio.
2. Do this on a **temporary copy**, never mutate the original stored photo
   file in `ItemData/<SKU>/` — Prime Directive 1 (raw data is permanent,
   never discard/overwrite). The downscaled version is a derived upload
   artifact only, not a replacement for the stored original.
3. If the resize itself fails for any reason (corrupt/unreadable image),
   don't mask it as a plain upload failure — let it surface distinctly
   (this may overlap with PP-DATAINTEGRITY-001's corrupt-photo detection;
   if you find one of the 10 SKUs is *also* corrupt rather than just
   oversized, note it in the result manifest, don't silently merge that
   into this fix).
4. Log what was resized (original dimensions → resized dimensions) so
   there's a durable trail per invariant C11 — this is a worker-level
   accommodation, not silent.

## Out of scope
- Don't change how photos are captured/stored originally — this is an
  upload-time accommodation only.
- Don't touch `thumbnail_gen`'s existing resize logic even if similar —
  different purpose (display thumbnails vs. eBay upload compliance),
  keep them separate unless you find they're trivially shareable without
  behavior risk to the existing thumbnail path.
- Requeuing the 10 dead-lettered jobs — separate step after merge.

## Dataset
No change to stored ItemData photos — the resize is upload-time-only, on
a temp copy, never persisted back to `ItemData/<SKU>/`.

## Acceptance (live)
1. Unit test: an image mocked/constructed above 15000px on one dimension
   gets resized before the (mocked) upload call receives it, and the
   resized dimensions are within the limit.
2. Unit test: a normal-sized image is unaffected — byte-identical bytes
   sent to the upload call (no unnecessary re-encoding/quality loss for
   the common case).
3. Unit test: the original file on disk is untouched after the function
   runs (dimension check via `Image.open` on the original path afterward
   still shows the original size).
4. Run the full offline suite — zero regressions.

## Quota/risk
None — no new API calls, purely local image processing before the
existing upload call.
