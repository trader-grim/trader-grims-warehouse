# IN PROGRESS — #1266/#1267 photo-integrity-mitigation legs 2/3

Branch: `todo/1266-1267-photo-integrity` (worktree
`/opt/TGW/var/worktrees/1266-1267-photo-integrity`). PP-DATAINTEGRITY-001.

Executing docs/ai-plans/photo-integrity-mitigation.md legs 2+3 per
tgw-coder's branch-per-task contract.

**Leg 2 (#1266):** built `src/tgw/integrity.py` — shared sha256
verify-after-copy helper (`verified_copy`/`verify_copy_tree`), stages a
copy next to the destination and only atomically renames it into place
once hash-verified, so a mid-copy kill can never leave a truncated file at
the destination path. Pre-flight found no existing "consolidation move"
code in-repo yet (PP-DRIVE-INDEX-001 Phase 3 checkbox unstarted) — noted a
pointer to the helper there for when that phase lands. Wired the helper
into the one real existing "usb restore" bulk-copy path
(`scripts/tgw-restore.sh --source usb`, replacing its `cp -rv`). SIGKILL
mid-copy acceptance test (`tests/test_integrity.py::test_verified_copy_refuses_success_on_sigkill`)
actually forks + kills a copy subprocess and asserts the destination never
appears — passing.

**Leg 3 (#1267):** wired `tgw.integrity.decode_verify_image()` (full PIL
`im.load()`, per the plan's own recommendation) into
`bundle_intake.py`'s `_handle_dir`/`_handle_zip` — corrupt camera files
are rejected before they're ever copied into ItemData; the rejection is
persisted as a `pipeline_error`/`photo_decode_rejected` finding on the
created item record (invariant C11), picked up automatically by the
existing generic `pipeline_error` catalog-verify surfacing in `api.py`.
Real truncated-JPEG acceptance test passing
(`tests/test_bundle_intake_decode_verify.py`).

Status: both legs implemented + tested with live corrupt/killed-copy
evidence. Writing result manifest next.
