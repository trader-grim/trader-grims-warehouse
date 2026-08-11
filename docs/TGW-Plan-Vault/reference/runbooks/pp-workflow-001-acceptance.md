# PP-WORKFLOW-001 — production acceptance checklist

**Owner:** shared; final operator acceptance: Dave

**Last verified:** 2026-08-10

**Applies to:** tgw-prod PP-WORKFLOW-001 rollout waves

**Last drill:** 2026-08-10

Use this checklist for each migrated seam. Passing source tests alone is not
production acceptance.

## Record the candidate

- [ ] Exact source commit and tree
- [ ] Immutable release generation, archive digest, content-manifest digest
- [ ] Independent source review result
- [ ] Exact flake commit and evaluated tgw-prod unit/config delta, if any
- [ ] Current rollback release and completed selection receipt
- [ ] Schema source/live parity and live table/index presence
- [ ] Effective worker user, ExecStart, EnvironmentFiles, WorkingDirectory
- [ ] Exactly one intended worker consumer

## Read-only baseline

- [ ] Health and failed-unit checks
- [ ] Queue counts by queue/state
- [ ] Per-canary Action Card captured
- [ ] Provider effects, observations, and authority rows inventoried
- [ ] Mixed-version targeted-sync inventory classified
- [ ] No ambiguous payload shapes
- [ ] No unresolved older provider effect for the canary
- [ ] Current selector values recorded

## Local treatment canary

- [ ] Dispatch is bound to SKU, graph, generation, condition, treatment/profile
- [ ] Exactly one job is created; exact duplicate is suppressed
- [ ] Worker claim and completion use exact unexpired lease token
- [ ] Canonical write uses generation CAS/journal
- [ ] SQLite projection is verified, including semantic no-op
- [ ] Receipt is immutable and fully bound
- [ ] Re-evaluation is atomic with completion when evidence changed
- [ ] No worker sleep, prerequisite retry-wait, or hard-coded successor enqueue

## Provider treatment canary

- [ ] Explicit current operator authority with exact scope
- [ ] Configured provider identity matches authority and worker binding
- [ ] Provider effect reserved and marked dispatched before the write
- [ ] Exactly one provider write
- [ ] Canonical marker contains the exact effect ID
- [ ] Crash/replay path reuses succeeded result without a second write
- [ ] Ambiguous-control path refuses resend and exposes reconciliation gate
- [ ] Post-push targeted sync is read-only and source-effect bound

## Timer and restart proof

- [ ] Future bounded `not_before` cannot be claimed early
- [ ] Timer payload retains exact immutable bindings
- [ ] Stale generation conflicts instead of overwriting
- [ ] Checkpoint round-trip is exact under PostgreSQL
- [ ] Wrong/expired/same-owner-old lease token cannot checkpoint or complete
- [ ] Crash after checkpoint resumes without repeating observation/model/provider work
- [ ] `REPAIR_REQUIRED` reconciles only its exact operation

## Operator surface

- [ ] Action Card shows fingerprints and evidence
- [ ] Unmet, explicit, waiting, active, reconciliation, and operator gates are truthful
- [ ] External legal action is held behind provider contract/authority
- [ ] Governed/ambiguous attempt has no Retry action
- [ ] Already-satisfied request is reported as satisfied, not held or dispatched
- [ ] No-dispatch held response reports reasons/gates, not false success

## Rollback drill

- [ ] Producer disabled first
- [ ] Governed queue/timers inventoried and drained or explicitly preserved
- [ ] Consumer disabled only after governed work is safe
- [ ] Prior immutable release selection succeeds with expected-current CAS
- [ ] Additive schema and immutable receipts/effects/observations remain
- [ ] No governed payload is interpreted by a legacy consumer

## Final decision

Acceptance requires all applicable checks, captured evidence, no unexplained
dead letters/reconciliation rows, and Dave's explicit operator acceptance.
Record intentional non-applicable checks rather than silently omitting them.
