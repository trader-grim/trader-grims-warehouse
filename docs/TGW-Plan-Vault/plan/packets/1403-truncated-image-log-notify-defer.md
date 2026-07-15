# Packet: ebay_draft should log+notify+skip on known truncated/corrupt photos, not loop into dead_letter

Todo: #1403   PP: PP-DATAINTEGRITY-001   Track: dead-letter triage (batch,
see PP-DEADLETTER-001.md — dispatched alongside 7 other packets this
round). **Rescoped by Dave, 2026-07-14 (see the todo's --note): this
image corruption is already-known/preexisting (leg 1's
`photo_files_readable` catalog-verify rule, #1154, already catches this
class — 206 bad files/149 SKUs found 2026-07-05). Do NOT attempt photo
repair or build the fuller legs 2/3 design (verify-after-copy,
decode-at-intake) here — those stay separately open. This packet is
narrowly: log it durably, notify, and defer/skip processing for the
affected item instead of leaving a bare unexplained dead_letter.**

## Context budget (ALL the model may load)
This packet + `src/tgw/workers/ebay_draft.py` (whole file, focus on
`_encode_resized()` lines 250-261 and its caller `_aspect_fill_photos()`
line 264+) + whatever existing invariant-C11-style "durable finding"
helper already exists in this codebase (grep for how #1154's
`photo_files_readable` rule persists its findings — reuse that pattern/
storage location rather than inventing a new one) + existing test file
for `ebay_draft.py` if one exists.

## Verified live before this packet was written
- 7-8 `ebay_draft` dead-letters: `OSError('image file is truncated (N
  bytes not processed)')`, plus 1 `OSError('broken data stream when
  reading image file')` — all from `Image.open(img_path)` in
  `_encode_resized()` (`ebay_draft.py:257`), called from
  `_aspect_fill_photos()`'s vision-photo-selection path. No `try/except`
  currently wraps this call — the `OSError` propagates all the way to a
  bare `HardFailure`/dead_letter with no durable finding recorded.
- Leg 1 of PP-DATAINTEGRITY-001's photo-integrity design
  (`photo_files_readable`, todo #1154, done 2026-07-05) already has a
  `catalog-verify` rule that detects exactly this class of corruption
  project-wide (206 bad files across 149 SKUs found). **Reuse that
  detection/recording mechanism rather than building a parallel one** —
  read how it persists findings (check `src/tgw/catalog_verify.py` for
  the rule and how it stores results) so this fix is consistent with it,
  not a second competing "corrupt photo" tracking scheme.

## Spec
1. Wrap the `Image.open()` call in `_encode_resized()` (or its caller) to
   catch `OSError` specifically for corrupt/truncated image files.
2. On catch: record a durable, queryable finding on the item (reuse the
   existing photo-integrity finding mechanism from #1154's leg 1 if it's
   item-addressable — don't just `log.warning()` and move on, that's
   exactly invariant C11's "skip/guard is a finding, not a log line").
3. Skip that specific photo for the vision aspect-fill call (fall back to
   the text-only prompt path, or continue with remaining readable photos
   if any) rather than letting the whole job dead-letter — the item can
   still get an ebay_draft result using its other readable photos, or the
   text-only fallback that already exists in this worker for
   non-cloud-vision providers.
4. Surface this durably enough that an operator/health-check can see "N
   items have unreadable photos blocking full aspect-fill" — check
   whether `tgw health` or `ops_digest.py` is the right existing surface
   to extend, rather than building a new one.

## Out of scope
- Photo repair/recovery — corrupt files stay corrupt, this packet does
  not attempt to fix or replace them.
- Legs 2 (verify-after-copy sha256) and 3 (decode-verify-at-intake) of
  the photo-integrity design — those remain open, separately scoped,
  not built here.
- Requeuing the affected dead-lettered jobs — separate step, and only
  useful once this fix means they'll actually make progress instead of
  hitting the same wall.

## Dataset
None — no photo files are modified, moved, or deleted. This is
detect-and-skip only, consistent with Prime Directive 1 (never
discard/overwrite raw data).

## Acceptance (live)
1. Unit test: `_encode_resized()` (or wherever you place the catch) called
   with a genuinely truncated/corrupt test image — confirm it doesn't
   propagate `OSError` uncaught, and confirm a finding gets recorded
   (assert on whatever the existing mechanism's storage/format is).
2. Unit test: `_aspect_fill_photos()` with a mix of one corrupt + several
   good photos — confirm the good ones are still used (job doesn't fail
   entirely over one bad photo).
3. Unit test: a normal all-good-photos item is unaffected (no behavior
   change, no spurious findings).
4. Run the full offline suite — zero regressions.
5. Confirm (live query against real data or a targeted test) that the
   finding is queryable the same way #1154's leg-1 findings are — don't
   just assert it exists in isolation, confirm it's discoverable through
   the existing photo-integrity surface.

## Quota/risk
None — purely local file-handling/logging change, no new API calls.
