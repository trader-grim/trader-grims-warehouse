# Result: 1399 ebay-upload-xml-parse-error
Status: done
Todo: #1399   PP: PP-DEADLETTER-001
Files touched:
- src/tgw/ebay/upload.py (extracted `_build_upload_payload()`, builds the
  UploadSiteHostedPicturesRequest XML via `xml.etree.ElementTree` instead
  of raw f-string interpolation, so `PictureName` text is XML-escaped)
- tests/test_ebay_upload_xml_escape.py (new)
- docs/TGW-Plan-Vault/plan/packets/results/1399-RESULT.md (this file)

Live evidence:
- Confirmed live (step 1 of spec, `ls` on the actual ItemData photo
  directories, not assumed) — all 3 affected SKUs' photo filenames
  contain a literal `&`:
  - `tgw201505301052553`: `Heartfelt Friends - Gramma & Grampa and Tabby Cat-{0..3}.jpg`
  - `tgw201809090823489`: `Better Homes & Gardens_ Wood Magazine -April 1999 Issue No. 114-{0..3}.jpg`
  - `tgw201808260939057`: `Car Muffler & Brake Embroidered Patch 2x3.25-Inches-{0..3}.jpg`
  This confirms the packet's diagnosis: unescaped `&` in `photo_path.stem`
  interpolated directly into the XML request body produced malformed XML,
  which eBay's Trading API parser correctly rejected with "XML Parse
  error." All 3 dead-letters share this exact pattern (100% of the 3, all
  from the same root cause — no other pattern needed investigation per
  spec step 3).
- Fix verified: `_build_upload_payload()` (new, extracted from
  `upload_photo()`) now builds the payload via `ET.Element`/`ET.SubElement`
  + `ET.tostring()`, which XML-escapes element text content automatically.
- New unit tests (`tests/test_ebay_upload_xml_escape.py`, 4 tests) confirm:
  - All 3 real confirmed-unsafe filenames (verbatim, including the `&`)
    round-trip through `ET.fromstring()` to the exact original string.
  - `<`/`>`/`"` also escape correctly (broader than just `&`).
  - The normal-ASCII-filename case produces identical structure/content
    (`PictureName`, `PictureSet=Supersize`, XML declaration prefix) — no
    regression to the common path.
  - Ran: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH python -m pytest -q tests/test_ebay_upload_xml_escape.py tests/test_ebay_upload_integrity.py` → `12 passed in 0.81s`
  - Full offline suite (same env override, confirmed importing from the
    worktree copy via `tgw.ebay.upload.__file__` check before running):
    `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH python -m pytest -q`
    → `2214 passed, 1 skipped, 1 warning in 419.00s` — zero regressions.

Deviations from spec: none. Spec offered a choice between hand-escaping
the single field (`xml.sax.saxutils.escape`) vs. switching payload
construction to `ET.Element`/`ET.tostring()`; chose the latter as it also
protects `<`/`>`/other special chars in `PictureName` beyond just `&`, and
is not meaningfully larger a diff than hand-escaping one f-string field.

Out-of-scope findings filed: none. No adjacent bugs found; the fix is
localized to the one interpolation point identified in the packet. The 3
dead-lettered jobs themselves were left un-requeued per the packet's
explicit "Out of scope" list (requeue is a separate post-merge step).
