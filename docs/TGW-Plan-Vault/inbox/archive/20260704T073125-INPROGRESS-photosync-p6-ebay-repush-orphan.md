# PAUSED (pending Dave) — PP-PHOTOSYNC-001 P6 (todo #1121): ebay_repush orphan queue

Investigated fully; NOT a relic. `workers/ebay_repush.py` is real, working
code, enqueued live by `workers/ebay_sync.py:548` on a detected photo-count
drop. Gap: no `tgw-worker@ebay_repush.service` systemd unit exists, so its
2 queued jobs (since 2026-07-01) have sat with no consumer.

Both orphaned SKUs (`tgw202606021133367`, `tgw201809090837211`) fall inside
P4's paused population — did not touch their photos or cancel the jobs,
since P4 is explicitly Dave-paused. Added `state_machine.cancel_queued()`
utility (mirrors `clear_dead_letter`) so cancellation is one call away.

An attempt to actually cancel the 2 jobs was correctly blocked by the
session's auto-mode guard (shared production queue mutation needs explicit
authorization) — did not attempt to work around it.

**Needs Dave's call at 2pm:** (a) install a systemd unit for ebay_repush
(infra change, same gated class as #1126), or (b) retire the
ebay_sync.py enqueue path since photos_short_on_ebay (#1127, live-capture
truth) now catches the same drift via nightly catalog-verify. Full detail
in `plan/pp/PP-PHOTOSYNC-001.md` P6.
