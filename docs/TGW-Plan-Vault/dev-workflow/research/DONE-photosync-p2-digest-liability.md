# DONE — PP-PHOTOSYNC-001 P2 (todo #1117): ops-digest pending-liability lines

RETRY_WAIT and MORNING EXPOSURE sections added to `tgw ops-digest`. Full detail
in `plan/pp/PP-PHOTOSYNC-001.md` P2 (updated with "what shipped" + one flagged
deviation: exposure grouped by queue_name, not quota pool — no queue→pool
mapping exists in the codebase to attribute accurately).

Live-verified: `tgw ops-digest` run 2026-07-03 19:5x showed real MORNING
EXPOSURE data (32 jobs across 7 queues). Unit tests added:
`tests/test_ops_digest_retry_exposure.py` (5 cases, all pass).

Could not seed a synthetic retry_wait row to demo the RED flag live — auto
mode classifier blocked the INSERT into the live `queue_jobs` table as an
unauthorized shared-resource write. Did not attempt to bypass; render logic
for that threshold is unit-tested instead.
