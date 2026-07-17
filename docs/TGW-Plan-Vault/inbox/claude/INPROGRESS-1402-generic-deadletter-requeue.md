# INPROGRESS: todo #1402 (PP-DEADLETTER-001)

Building a generic, parameterized version of
`scripts/requeue_ebay_draft_402_dead_letters.py` (queue name + error-pattern
match as CLI args, preserving the exact job_id-dedupe + run-once-marker
safety pattern) so the four transient-only dead-letter buckets identified in
`docs/TGW-Plan-Vault/plan/pp/PP-DEADLETTER-001.md` (ebay_legacy_sync,
ebay_sync, ebay_sku_migrate, ebay_publish) can be verified-and-requeued
without a bespoke script per queue. Live-verified via psql against
queue_jobs on 2026-07-17 that counts have grown since the 2026-07-14 triage
snapshot but the error classes are unchanged; ebay_sync and ebay_publish
each also contain a real-bug row (400 offer-endpoint / Brand-missing) that
must NOT be swept up by the pattern match — confirmed the per-queue error
patterns from the PP doc exclude those rows correctly. Working in isolated
worktree at /opt/TGW/var/worktrees/1402-generic-deadletter-requeue on
branch todo/1402-generic-deadletter-requeue.
