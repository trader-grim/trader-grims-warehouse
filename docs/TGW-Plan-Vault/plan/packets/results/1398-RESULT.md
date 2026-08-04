# Result: 1398 ebay-upload-dimension-limit
Status: done
Todo: #1398   PP: PP-DEADLETTER-001
Files touched:
- src/tgw/ebay/upload.py
- tests/test_ebay_upload_dimension_limit.py (new)
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1398-ebay-upload-dimension-limit.md (breadcrumb, worktree-local)

Live evidence:
- Targeted new tests: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src pytest -q tests/test_ebay_upload_dimension_limit.py` → `5 passed in 0.93s`, confirmed testing the worktree copy (`tgw.ebay.upload.__file__` resolved under the worktree path, not the shared checkout).
- Full offline suite: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src pytest -q` → `2215 passed, 1 skipped, 1 warning in 265.03s (0:04:25)` — zero regressions.
- Implementation: `upload_photo()` in `src/tgw/ebay/upload.py` now calls a new `_prepare_upload_bytes()` helper before building the multipart POST. Images within eBay's 15000px-per-side limit (taken verbatim from the live dead-letter error text: "File dimension limit exceeds 15000 pixels.") are sent byte-identical, unchanged. Oversized images are downscaled on an in-memory (`io.BytesIO`) copy only — aspect ratio preserved via `Image.LANCZOS` resize — and the resized bytes are what's POSTed; the file on disk (`photo_path`) is never opened for writing. A resize is logged both via `log.info` and a durable structured event (`tgw_logging.log_event('ebay_upload_photo_resized', photo=..., original_dimensions=..., resized_dimensions=..., max_dimension_px=15000)`) for invariant C11 traceability. If Pillow can't decode/resize the image at all, a new `PhotoResizeError(RuntimeError)` is raised distinctly rather than masking it as a plain eBay upload rejection.

Deviations from spec: none. One judgment call flagged: eBay's documented max-dimension constant wasn't independently re-verified against eBay's published API docs (the context budget for this packet was scoped to upload.py + thumbnail.py + ebay_upload.py only, no reference-doc reads) — `_MAX_DIMENSION_PX = 15000` is taken directly from the live dead-letter error text itself ("File dimension limit exceeds 15000 pixels."), which is the authoritative live-observed value per the packet's own "Verified live" section, so this is treated as confirmed rather than assumed.

Out-of-scope findings filed: none. Did not independently re-check whether any of the 10 currently-dead-lettered SKUs' photos are *also* corrupt (vs. purely oversized) — that would require opening the actual dead-lettered SKUs' photo files, which falls outside this packet's declared context budget (upload.py/thumbnail.py/ebay_upload.py only) and outside its scope (packet explicitly defers "requeuing the 10 dead-lettered jobs" to a separate post-merge step). If a requeue after merge surfaces a `PhotoResizeError` for any of the 10, that's the distinct signal this fix was built to produce, and it should be triaged against PP-DATAINTEGRITY-001 separately rather than folded back into this fix.
