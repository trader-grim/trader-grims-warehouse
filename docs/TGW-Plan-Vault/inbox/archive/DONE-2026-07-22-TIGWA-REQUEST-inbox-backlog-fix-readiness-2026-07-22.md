# Request — verify inbox-backlog remedy readiness

**From:** Tigwa
**To:** Claude
**Date:** 2026-07-22T17:46:45Z
**Context:** Dave inquired after hearing that Tigwa might have roughly 94 pending inbox files. The live check shows 54 mixed artifacts in `inbox/tigwa/`, all with the same 2026-07-21T23:43:02Z mtime, plus separate root/queued/archive populations. This is not evidence of 94 current actionable assignments.

Dave believes the apparent accumulation may be attributable to his deliberate financial caution: the supervised PM-intake/librarian path has remained paused rather than automatically filing/enqueuing material. Treat that as an operator hypothesis to verify, not as a settled root-cause claim.

Please verify whether the **proposed inbox-backlog remedy** is ready for implementation. In particular:

1. Identify the exact current proposal/PP/todo and source artifacts that constitute the remedy; distinguish the already-implemented #1435/#1436 topology/dry-run work from any still-proposed supervised filing, taxonomy, review, or intake-worker activation step.
2. State whether it is genuinely implementation-ready now. Check for unresolved authority, shelving taxonomy, review/acceptance, cost/model, operational-load, delivery, and rollback/recovery gates.
3. If it is ready, return the smallest safe implementation packet: scope, excluded paths/actions, actor, prerequisites, acceptance evidence, rollback, and post-implementation low-cost monitoring behavior.
4. If it is not ready, name the exact missing decision/evidence—not a generic caution—and propose the shortest no-mutation path to resolve it.

**Boundary:** This is a readiness review only. Do not activate `pm_intake`, run `admin-file` for real, alter Plan Vault artifacts, change services/configuration, spend model/provider funds, or modify task state as part of the review.
