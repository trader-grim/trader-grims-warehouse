# Request — define every worker used in the TGW pipeline

**From:** Tigwa / Dave-directed HR admission rule
**To:** Claude
**Program:** PP-HR-001, PP-AGENT-DISCIPLINE-001, relevant pipeline PPs
**Status:** inventory and definition only; no service/config/queue/credential change authorized.

Dave asks that **each worker actually used in the TGW pipeline be defined**. Do not infer the roster from names, stale docs, or installed units: establish it from current source/configuration plus safe live read-only runtime/queue evidence, and separate active, inactive, retired, planned, and unknown workers.

For every actual pipeline worker, stage a source-linked worker card containing:

1. **Identity and ownership:** stable name, implementation/service/queue, code/config source, owner/boss, version/hash, active state, and revalidation trigger.
2. **Job description:** narrow task class; trigger/inputs; permitted outputs; side effects; data/credential access; budget/quota/time limit; idempotency/dedupe/concurrency boundary; required result evidence; reviewer/acceptance route; escalation/stopping rules; and explicit prohibitions.
3. **Observed resume:** real observed runs/tests, task packet types, success/failure/dead-letter behavior, recovery/rework history, cost/latency/quota where applicable, evidence quality, and confidence by task class. Mark missing evidence `LIMITED-EVIDENCE` or `UNKNOWN`; do not substitute design claims.
4. **Operating contract:** delivery/queue semantics; authority boundary; source-of-truth/state ownership; monitoring/health/dead-letter reporting; retry/timeout/fallback limits; incident/disable procedure; and retention/provenance requirements.

Explicitly distinguish regular `tgw-worker@*` processes, helper scripts, agentic CLI workers, MCP bridges, model/provider routes, and planned-but-not-admitted workers. A component with an executable but no current work contract is `NOT-ADMITTED`, not implicitly usable.

Return a compact roster plus staged per-worker cards or a clearly versioned scalable schema and the first representative cards. Flag where definitions contradict actual permissions, queue behavior, credentials, or code. Do not start/stop/replay workers, mutate queues/tasks/plans, alter credentials, or spend provider funds during this inventory.
