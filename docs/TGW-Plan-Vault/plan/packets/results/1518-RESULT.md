# Result: 1518 ocr-sweep-track-r3
Status: done
Todo: #1518   PP: PP-KNOWLEDGE-001

## Files touched
- `reference/recoll-ocr-filter/rclimg_ocr.py` (new) — the missing recoll indexing
  filter: an `RclBaseHandler`-shaped, rclexecm-persistent-protocol handler (same
  shape as recoll's own bundled `rclimg.py` EXIF-tag filter) that calls into
  recoll's bundled `rclocrtesseract.py` (`ocrpossible()`/`runocr()`) to OCR an
  image and emit the text as a minimal HTML document — recoll's convention for
  extracted text. Resolves recoll's own filters directory at runtime via the
  `recollindex` binary actually on `PATH` (no hardcoded Nix store path to go
  stale on a recoll version bump). Full deployment instructions for wiring this
  into the live `/opt/TGW/.recoll` config are in the file's own header comment.
  No `src/tgw/` code was touched — `search_full.py`/`mcp_server.py` (Track
  R2/#1147) needed no changes; they already work against any recoll index
  pointed at by `RECOLL_CONFDIR`, OCR-enabled or not.

## Pre-flight findings (live-verified before writing any code)
1. **"tesseract via recoll filter" — the filter half was NOT already wired,
   and the naive wiring silently does nothing.** Recoll 1.43.2 (installed,
   `/nix/store/...-recoll-1.43.2`) ships `rclocrtesseract.py` (the actual OCR
   logic) and `rclocr.py` (an orchestrator), but neither speaks recoll's
   `execm` persistent wire protocol on its own — `rclocr.py` is a one-shot
   script meant to be called BY another persistent filter (e.g. `rclpdf.py`
   falling back to OCR for a scanned PDF with no extractable text), not to be
   registered directly against an image mimetype. Doing so anyway (`mimeconf:
   image/jpeg = execm rclocr.py`) produces `rclocr: Usage: rclocr.py
   <imagefilename>` / `MHExecMultiple: getline error` in the indexer log —
   the document still gets ADDED to the index (docid assigned, no fatal
   error), just with zero OCR text, so this failure mode is silent unless the
   indexer log is read at debug level 5+. Confirmed live by reproducing it
   first, then building the real fix (`rclimg_ocr.py`, this packet's
   deliverable).
2. **A second silent bug, also found live**: recoll's `execm` protocol hands
   the filename to the handler as `bytes`. `rclocrtesseract.ocrpossible()`
   does a str-only `os.path.splitext(path)[1].lower() in _okexts` check
   (`_okexts` is a tuple of `str`) — a `bytes` path never matches, so
   `ocrpossible()` silently returns `False` for every real image, again with
   no error surfaced anywhere. Fixed in `rclimg_ocr.py` by decoding to `str`
   before calling into `rclocrtesseract`. Root-caused via a direct debug
   probe (dumped `RECOLL_CONFDIR`, the raw `filename` value, and
   `ocrpossible()`'s return) rather than guessed at.
3. **`tesseract` itself is genuinely not installed anywhere on tgw-prod.**
   `which tesseract` → not found; not referenced by any `.nix` file in this
   repo or (checked) `/home/db/tgw-flake/flake.nix`. `nixpkgs#tesseract`
   (5.5.0, with `leptonica`, ~470 MB) is fetchable via `nix shell
   nixpkgs#tesseract` — used for this packet's proof-of-mechanism (absolute
   `tesseractcmd` path into the fetched Nix store closure) — but that's a
   per-session fetch, not a durable install. Filed as #1524 (system-flake
   change, out of scope for a branch-scoped worktree task — see below).
4. **Live recoll config (`/opt/TGW/.recoll/recoll.conf`) explicitly excludes
   ItemData/ItemCatalog photos today** — `topdirs` doesn't include
   `ItemData` at all, and `ItemCatalog/thumbnails` is explicitly
   `skippedPaths`-excluded with the comment "photos are not in scope for
   Phase 0." Track R3 is exactly the phase that lifts this, but per the
   packet's own thermal-aware framing, that lift is NOT done in this packet
   (see "What the full-fleet sweep needs" below) — confirmed the exclusion is
   real and current before assuming otherwise.

## Live evidence
Two REAL ItemData photos (not synthetic test images), copied read-only into a
scratch sample dir, indexed into a throwaway test recoll config (NOT the live
`/opt/TGW/.recoll` index — nothing in this packet touches the live index or
its 441K+ existing docs):

- `tgw202606021107459/tgw20260602_162457.jpg` — a photographed catalog page
  (Bob Drake Ford-parts catalog item). Contains the order code **`40-8200-K`**
  printed on the page — verified NOT present anywhere in the item's own JSON
  (`title`/`description` mention "Bob Drake" but not this specific order
  code) — a genuine label/serial-class string only recoverable from the photo.
- `tgw202605131827555/tgw20260602_110718.jpg` — a photographed book contents
  page (cross-stitch pattern book item). Contains **"Gingerbread Gift Tags"**
  and **"Scandinavian Christmas"** (chapter titles) — verified NOT present in
  the item's JSON (`title`/`description` only say "225 festive cross-stitch
  designs").

Raw `tesseract` sanity check (before building the recoll filter, to confirm
OCR quality on real photos):
```
$ tesseract tgw20260602_162457.jpg stdout -l eng | grep -i "40-8200"
Order 40-8200-K P
Order 40-8200-K. Save S
40-8200 ChromeGrille ~ $2,450 52,250 b

$ tesseract tgw20260602_110718.jpg stdout -l eng | grep -i "gingerbread\|scandinavian"
Scandinavian Christmas
Gingerbread Gift Tags
```

End-to-end through the REAL `tgw.search_full.run_full_text_search()` /
`tgw_search_full` MCP tool code (Track R2/#1147's actual functions, called
directly with `RECOLL_CONFDIR` pointed at the test index — same `recollq`
invocation and result-parsing code that `tgw search --full-text` uses in
production, only the index location differs):
```
=== 40-8200-K ===
1 result(s) for '40-8200-K' (22 ms)
  [image/jpeg] tgw20260602_162457.jpg (1078162 bytes)
      file:///.../sample/tgw20260602_162457.jpg

=== Gingerbread Gift Tags ===
1 result(s) for 'Gingerbread Gift Tags' (24 ms)
  [image/jpeg] tgw20260602_110718.jpg (645964 bytes)
      file:///.../sample/tgw20260602_110718.jpg

=== Scandinavian Christmas ===
1 result(s) for 'Scandinavian Christmas' (22 ms)
  [image/jpeg] tgw20260602_110718.jpg (645964 bytes)
      file:///.../sample/tgw20260602_110718.jpg

=== MCP tool tgw_search_full ===
40-8200-K -> ok True count 1 file:///.../tgw20260602_162457.jpg
Gingerbread -> ok True count 1 file:///.../tgw20260602_110718.jpg
```

Full pytest suite (worktree copy, `PYTHONPATH`/`LD_LIBRARY_PATH` override
confirmed — `tgw.search_full.__file__` resolved under the worktree path
before testing): **2538 passed, 1 skipped, 0 failed** — no regressions. (No
`src/tgw/` files were modified by this packet, so no regression was expected;
ran the full suite anyway per the packet's instruction.)

## What the full-fleet sweep would need (explicitly NOT done here)
Per the packet's own framing, this proves the mechanism only — filed as #1525:
- **Tesseract on PATH durably** — currently only fetchable per-session via
  `nix shell nixpkgs#tesseract`; needs a real flake package addition
  (`/home/db/tgw-flake`), filed as #1524, gates #1525.
- **Wire `rclimg_ocr.py` into the LIVE `/opt/TGW/.recoll/mimeconf` +
  `recoll.conf`** (`ocrprogs = tesseract`, `tesseractcmd = <durable path>`) —
  exact snippet is in the new file's header comment.
- **Do NOT add `ItemData` as a recoll `topdir`.** `recollindex` walks every
  configured topdir on every reindex; ItemData's full photo volume is exactly
  the thermal/I/O risk the design doc and this packet's spec both flag.
  Instead: either (a) batch via `recollindex -i <files...>` (indexes
  individual files, no topdir walk, no full-database purge/reindex), scheduled
  in small thermal-aware batches, or (b) index a bounded, explicitly-listed
  sub-sample topdir, never the live `ItemData` tree wholesale.
- **Estimated scope**: ~55k items (per CLAUDE.md's scale-context reference),
  several photos each — likely 150k-300k+ images. At real-world tesseract
  throughput (roughly 1-3s/image on this hardware, single-threaded, per the
  two live samples above: ~1-2s each) that's tens of hours of CPU time —
  squarely the case for a1131's read-only NFS mount
  (`/opt/TGW/mnt/tgw-prod/data`) per the design doc's own explicit
  recommendation ("run on cool days or from a1131 over the ro NFS mount"),
  not tgw-prod itself. A second, a1131-local index of the NFS view (as the
  design doc suggests as "the experiment") keeps zero risk to the primary
  441K-doc index while this is validated at scale.
- **OCR cache**: recoll's `ocrcachedir` config key (not set in this packet's
  test config) avoids re-OCRing unchanged files on every reindex — worth
  setting explicitly for the full sweep given the CPU cost above.

## Deviations from spec
1. **Built a new filter script (`rclimg_ocr.py`) rather than using recoll's
   bundled tesseract filter "as-is."** The todo/design-doc phrasing ("tesseract
   via recoll filter") reads as if recoll's bundled tesseract support just
   needs turning on. Verified live it does not work out of the box (see
   pre-flight findings 1-2 above) — the bundled `rclocrtesseract.py` is a
   helper module, not a directly-registrable filter, and has its own latent
   bytes-vs-str bug. This is a real gap in what "recoll filter" provides, not
   a design choice on my part; flagging per Prime Directive 3.
2. **No live-index wiring, no full-fleet sweep** — per the packet's own
   explicit instruction, only a small real-photo proof-of-mechanism was done;
   full-fleet wiring is filed as #1525 (thermal-gated, a1131-NFS-mount
   candidate) rather than guessed at, per the packet's explicit fallback
   instruction ("stop and report rather than guessing at what 'full sweep'
   should look like").
3. **`tesseract` obtained via a per-session `nix shell nixpkgs#tesseract`**
   rather than a system-flake install, since a durable install requires
   editing `/home/db/tgw-flake` (outside this worktree's file/path scope,
   and outside a single branch-scoped packet's authority per CLAUDE.md's
   "system/flake stays Claude's [interactive-session] responsibility"
   framing) — filed as #1524.
4. **Mimeconf mapping replaces, rather than augments, the default EXIF-tag
   `rclimg` handler for `image/jpeg`/`png`/`tiff`.** Track R3 is specifically
   about OCR text; since these mimetypes weren't indexed at all before this
   packet (see pre-flight finding 4 — ItemData/thumbnails were excluded), no
   existing EXIF-tag search capability is lost by this choice. Noted as a
   design choice, not silently made — a future packet could extend
   `rclimg_ocr.py` to run both extractions and concatenate if EXIF/XMP tags
   turn out to matter too.

## Out-of-scope findings filed
- #1524 (PP-KNOWLEDGE-001): add `tesseract` to the system flake
  (`/home/db/tgw-flake`) for a durable on-PATH install.
- #1525 (PP-KNOWLEDGE-001): the actual full-fleet OCR sweep — wiring
  `rclimg_ocr.py` into the live `/opt/TGW/.recoll` config and running it
  against the real ItemData photo set, thermal-gated, likely from a1131's ro
  NFS mount. Gated on #1524.
