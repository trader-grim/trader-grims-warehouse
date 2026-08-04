Todo #1303 (PP-COHESION-001, invariant C11) — DONE, stitched. ebay_upload.py's
no-photos-on-disk guard now persists `ebay_upload_blocked` on the item
(self-healing clear on next full success); new `ebay_upload_no_photos_unrepaired`
catalog-verify rule (critical). Reviewed clean, full suite green. Filed
todo #1374 (PP-NIXOS-001) for a real operational-friction finding
(psycopg2/libz.so.1 LD_LIBRARY_PATH gap in worktree pytest runs).
