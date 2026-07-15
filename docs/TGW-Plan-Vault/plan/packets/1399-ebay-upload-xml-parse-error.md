# Packet: ebay_upload dead-letters with eBay-side "XML Parse error" — likely unescaped filename in our request body

Todo: #1399   PP: PP-DEADLETTER-001   Track: dead-letter triage (batch, see
PP-DEADLETTER-001.md — dispatched alongside 7 other packets this round)

## Context budget (ALL the model may load)
This packet + `src/tgw/ebay/upload.py` (whole file, 100 lines) + item JSON
for the 3 affected SKUs listed below (`ItemData/<SKU>/<SKU>.json`, via
`tgw item get` or the fence — read-only, do not edit) + their actual photo
filenames on disk. Nothing else.

## Verified live before this packet was written
- 3 `ebay_upload` dead-letters: `RuntimeError('all photo uploads failed
  for <SKU>: UploadSiteHostedPictures failed (Failure): XML Parse
  error.')` — SKUs: `tgw201505301052553`, `tgw201809090823489`,
  `tgw201808260939057`.
- Critically: this is **eBay's own Trading API telling us our request XML
  didn't parse**, not our code failing to parse eBay's response. Look at
  `src/tgw/ebay/upload.py:53-59`:
  ```python
  xml_payload = (
      '<?xml version="1.0" encoding="utf-8"?>'
      f'<UploadSiteHostedPicturesRequest xmlns="{_NS}">'
      f'<PictureName>{photo_path.stem}</PictureName>'
      '<PictureSet>Supersize</PictureSet>'
      '</UploadSiteHostedPicturesRequest>'
  )
  ```
  `photo_path.stem` (the photo's filename without extension) is
  interpolated **directly into the XML body with no escaping**. If any of
  the 3 SKUs' photo filenames contain an XML special character (`&`,
  `<`, `>`, or a raw `"`/`'` — though those are less likely to break an
  element's text content specifically), the resulting request body is
  malformed XML and eBay's parser correctly rejects it. This is the same
  class of unescaped-interpolation bug already fixed multiple times this
  project (`build_listing_description()`'s `ai_desc`/picklist-line
  escaping, todos #1276/#1367 earlier this session) — verify it applies
  here the same way before assuming it, don't just pattern-match blindly.

## Spec
1. Check the actual photo filenames for the 3 affected SKUs (list the
   photo directory, don't guess) — confirm whether any contain `&`, `<`,
   `>`, or other XML-unsafe characters. This is the verification step;
   don't skip it even though the code strongly suggests this is the bug.
2. If confirmed: XML-escape `photo_path.stem` (use `xml.sax.saxutils.escape`
   or Python's `xml.etree.ElementTree` to build the payload instead of
   raw f-string interpolation — check whether switching the whole payload
   construction to `ET.Element`/`ET.tostring()` is cleaner than
   hand-escaping a single field; either is acceptable, pick based on
   the smallest diff that's still correct).
3. If NOT confirmed (filenames are clean): investigate further — do not
   force this diagnosis onto data that doesn't support it. Check for
   another XML-unsafe field, or whether eBay intermittently returns this
   message for a different reason (network truncation of the multipart
   body, etc.) and report your actual finding.

## Out of scope
- `PictureSet`/other hardcoded XML literals — those are static strings,
  not variable interpolation, no risk there.
- Requeuing the 3 dead-lettered jobs — separate step after merge.
- Renaming the actual photo files on disk — if the filename itself is the
  problem, the fix is escaping it in the XML request, not renaming stored
  files (Prime Directive 1 — never touch raw stored data for this kind of
  fix).

## Dataset
None — request-construction fix only, no stored files touched.

## Acceptance (live)
1. Unit test: `upload_photo()` (or a testable helper you extract for the
   payload construction) called with a photo path whose stem contains
   `&` — confirm the resulting XML payload is well-formed (parseable by
   `xml.etree.ElementTree.fromstring`) and the `PictureName` text content
   decodes back to the original unescaped filename.
2. Unit test: a normal ASCII filename with no special characters produces
   byte-identical (or semantically identical) XML to the current
   behavior — no regression to the common case.
3. Run the full offline suite — zero regressions.
4. Report in the result manifest which of the 3 SKUs' filenames actually
   contained the unsafe character(s), with the literal filename shown.

## Quota/risk
None — local fix, no new API calls in the fix itself. Verification step
(checking filenames) is a local filesystem read via the fence, not an
eBay call.
