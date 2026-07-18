# Result: #1266/#1267 photo-integrity-mitigation legs 2/3
Status: done
Todo: #1266, #1267   PP: PP-DATAINTEGRITY-001

Files touched:
- `src/tgw/integrity.py` (new) — shared leg 2 (`verified_copy`,
  `verify_copy_tree`, `sha256_file`, `CopyIntegrityError`, CLI) + leg 3
  (`decode_verify_image`) helpers
- `src/tgw/workers/bundle_intake.py` — `_handle_dir`/`_handle_zip` now
  decode-verify images before they reach ItemData; new `_decode_verify`/
  `_log_rejected` helpers; `_write_item_json` persists a
  `pipeline_error`/`photo_decode_rejected` finding when any image was
  rejected
- `scripts/tgw-restore.sh` — `--source usb` copy step now calls
  `python3 -m tgw.integrity copy-tree` instead of bare `cp -rv`
- `docs/TGW-Plan-Vault/plan/PP-DRIVE-INDEX-plan.md` — Phase 3 checklist
  item now points at the shared leg-2 helper for when consolidation-move
  code is actually written
- `tests/test_integrity.py` (new) — 7 tests incl. the SIGKILL mid-copy
  acceptance test
- `tests/test_bundle_intake_decode_verify.py` (new) — 2 tests with real
  truncated-JPEG rejection + all-corrupt hard-failure
- `tests/conftest.py` — added `make_fake_create_item` fake-fence helper
  (test infra only, follows the existing `make_fake_patch_item` pattern)

Live evidence:

**Leg 2 — SIGKILL mid-copy** (`tests/test_integrity.py::test_verified_copy_refuses_success_on_sigkill`,
also reproduced manually): spawned a subprocess copying a 64 MiB file with
an artificial per-chunk delay, `kill -9`'d it ~0.4s in (well before
completion), then checked the filesystem directly:
```
exit status: 137   (subprocess killed by SIGKILL)
final dest exists?  NO-correctly-absent
$ ls -la <scratch>
-rw-r--r-- ... 67108864 bigsrc.bin
-rw-r--r-- ...  6291456 .tmp-verify-229645-bigdest.bin   <- orphaned partial staging file, 6MiB of 64MiB
```
`bigdest.bin` (the real destination path) never appeared — only the
`.tmp-verify-*` staging file, at ~10% of source size, proving the kill
landed mid-copy and the helper never renamed a partial file into the
"success" path. `pytest` run: `7 passed` in `tests/test_integrity.py`.

**Leg 2 — usb-restore wiring, live tree copy**: built a scratch USB-vault
layout (`dumps/`, `secrets/`) and ran
`python3 -m tgw.integrity copy-tree <src> <dest>` against it — both trees
copied with matching sha256 (`diff` of `sha256sum` output empty on both).
`scripts/tgw-restore.sh --dry-run --source local`: unchanged expected-fail
behavior (`Error: no PostgreSQL dump found ...`), `bash -n` clean.

**Leg 3 — real corrupt-file rejection**
(`tests/test_bundle_intake_decode_verify.py::test_corrupt_photo_rejected_good_photo_kept`):
a bundle dir with one valid JPEG and one genuinely truncated JPEG (source
bytes cut to 1/3 length — same failure class as the Feb-2022 incident).
`worker._handle_dir()` run against it:
- `good.jpg` copied into ItemData; `bad.jpg` never copied at all
- created item JSON has
  `pipeline_error: {code: "photo_decode_rejected", detail: "bad.jpg: ...", source: "bundle_intake"}`
  and `photo_decode_rejected: [{"file": "bad.jpg", "error": "..."}]`
- log line observed: `bundle_intake decode-verify rejected bad.jpg for
  tgw20260101000000001: Truncated File Read` (PIL's own exception text —
  confirms full `im.load()` caught the tail truncation, which header-only
  `im.verify()` would have missed)
`test_all_photos_corrupt_hard_fails_without_creating_item`: an
all-corrupt bundle raises `HardFailure` and creates nothing (`created ==
[]`), matching the pre-existing "no images in bundle" hard-failure
pattern rather than silently producing an empty item.
Full suite: `pytest tests/test_integrity.py
tests/test_bundle_intake_decode_verify.py tests/test_multi_intake.py
tests/test_pm_intake.py` → `61 passed`. Confirmed testing the worktree's
own copy (not the shared checkout) via
`LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH
PYTHONPATH=/opt/TGW/var/worktrees/1266-1267-photo-integrity/src:$PYTHONPATH`
and `python -c "import tgw.integrity as m; print(m.__file__)"` resolving
under the worktree path.
`ruff check src/tgw/integrity.py src/tgw/workers/bundle_intake.py` → all
checks passed.

Deviations from spec:
- The plan doc names "usb-restore, consolidation moves" as leg 2's two
  target callers. Pre-flight (invariant C11 live-verify step) found: (a)
  no photo-specific "usb-restore" script exists — the only real
  "usb restore" bulk-copy code in the repo is `scripts/tgw-restore.sh
  --source usb`, which restores Postgres dumps + secrets, not photos —
  wired the helper in there since it's the literal existing match for the
  name; (b) "consolidation moves" (PP-DRIVE-INDEX-001 Phase 3 — "Move/copy
  files to canonical locations") has no code yet at all (Phase 3 is an
  unchecked checklist item, not a built feature) — nothing to wire the
  helper into. Built the shared helper as the deliverable and left a
  pointer in `PP-DRIVE-INDEX-plan.md` Phase 3 for when that phase's move
  code is actually written, rather than fabricate a consolidation-move
  code path that doesn't exist to satisfy the letter of "wire it into
  both callers." Flagging this explicitly rather than silently declaring
  it done against a caller that isn't real yet.
- Leg 3's decode-verify was wired into `bundle_intake.py`'s `_handle_dir`
  and `_handle_zip` (the two formats that actually copy/extract camera
  photos into ItemData); the `symlink` format (`_handle_symlink`) was left
  untouched since it never copies bytes — the item already exists in
  ItemData and its photos would already have gone through this same path
  on however it originally landed there.
- When ALL images in a bundle fail decode-verify, the worker raises
  `HardFailure` (dead-letters the job) rather than creating an item with
  zero photos — this mirrors the pre-existing "no images in bundle"
  hard-failure behavior for the zero-images case and was not itself
  spec'd by the plan doc (which only requires *a* persisted finding for
  *a* corrupt file, tested via the partial-rejection case above). Not
  silent: `_log_rejected` logs + emits a `bundle_intake_photo_rejected`
  structured event before the HardFailure is raised, so the dead-letter
  job's error plus that event both carry the reason.

Out-of-scope findings filed: none — the one adjacent gap found
(PP-DRIVE-INDEX-001 Phase 3 has no consolidation-move code yet) is already
an existing open checklist item in `PP-DRIVE-INDEX-plan.md`, not a new
finding; annotated it in place rather than filing a duplicate todo.
