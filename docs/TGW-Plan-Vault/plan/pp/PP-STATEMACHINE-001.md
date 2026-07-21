# PP-STATEMACHINE-001 — job manifest contract, enforced by the state machine itself — NEW 2026-07-20

**Origin:** surfaced during PP-SOLD-001's #1604/#1605/#1607 incident chain (multi-order
data-loss bug, `ebay_legacy_sync` restore, lease-expiry race, 451-row duplicate-job
backlog). Confirmed via `PP-DEADLETTER-001`'s own 2026-07-14 snapshot that 148
`ebay_legacy_sync` dead-letters were already present then and waved off as
"transient/lease-related, all clear now" — this bug was recurring and being dismissed
as noise for at least a week before being root-caused.

Dave, 2026-07-20, after the audit found the same missing-`dedupe_key` hole in 8
self-rescheduling workers (not the 5 originally suspected): "not missing, just missed
by the second coder" — the fix has to be structural, not a per-call-site patch that the
next new worker can just as easily skip. "This is state machine, not job contracts...
we can call it and handle it as a manifest but the state machine is the enforcer."

## Scope

`src/tgw/queue/state_machine.py`'s `enqueue_job()` is the single point every job enters
the system through. This PP defines the **manifest** — the properties every job must
declare when issued — and makes `enqueue_job()` itself the **enforcer**, rejecting
incomplete manifests at call time rather than silently accepting them (the same failure
class as the missing `dedupe_key` calls: a rule that lived only in a docstring warning,
not in something that fails loudly).

## The manifest (as understood today — extend this list as new gaps are found)

1. **`dedupe_key`** — required. Reject semantics (default) for per-entity work where a
   second identical request is genuinely a no-op; `debounce=True` extend semantics for
   singleton/batch-coalescing triggers (already-working pattern: `catalog_rebuild:pending`).
   No call site may omit this silently — a genuine exception must be named explicitly,
   not defaulted into.
2. **`priority`** — required in effect, satisfiable by a config default. New file
   `tgw-queue-priorities.json`, same shape/convention as `tgw-models.json` (`defaults`
   block of named tiers: `urgent`=10, `high`=30, `normal`=100 [today's implicit default],
   `low`=200; entries keyed `"<queue_name>:<operation>"` pointing at a tier via
   `{"use_default": "<name>"}` or an explicit int). `enqueue_job()` looks this up when the
   caller doesn't pass `priority` explicitly; an explicit `priority=` argument is the
   manifest-level override and always wins. Falls back to `normal` (100) if no config
   entry exists — undocumented gaps don't break, they just don't get special treatment
   until someone deliberately adds an entry.
3. **`entity_id`/`entity_type`** — required whenever a job is about a specific item
   (`entity_type='item'` demands a non-empty `entity_id`). Already flagged as a real gap
   in `enqueue_job()`'s own docstring ("forgetting to pass entity_id... silently breaks
   `tgw queue-history --sku <sku>`") — same shape of risk as `dedupe_key`, never
   mechanically enforced. Roll into the same audit/enforcer pass.
4. **`supersede`** (new) — declares whether this job must become eligible immediately
   regardless of an existing pending job under the same `dedupe_key` (today's debounce
   collision can only push a pending job's `not_before` *later* via `GREATEST()`, never
   earlier). `supersede=True` atomically cancels the existing pending row for that key
   and inserts a fresh, immediately-eligible one instead. This is the fix for the
   `restart-ebay-token` CLI's "force now" need (flagged as item D.1 in #1607's audit) —
   folded in here as a manifest property, not a one-off special case.
5. **`depends_on`** (new, NOT YET BUILT — design captured 2026-07-21) — for job
   sequences (e.g. a 3-part draft→upload→publish chain, or any multi-step operation)
   where job N+1 must not become claimable until job N has actually completed, not
   merely been ordered ahead of it. `priority` alone only fixes *claim order* among
   ready jobs in a single-worker queue — it says nothing about completion. Dave,
   2026-07-20/21 (Perplexity-adjacent design conversation): "I was thinking a job is
   registered when submitted, manifest hashed for id, and excluded when completed."
   Design, reusing existing infrastructure rather than adding a parallel one:
   - A job's identity for dependency purposes is its own `dedupe_key` — no new hash/ID
     needed, since `dedupe_key` is already a deterministic manifest-derived string and
     is exactly what's already unique-indexed per active state (`uq_queue_jobs_dedupe_
     key_active`, active-state-only per the #1608 build).
   - A dependent job is submitted with `depends_on=<precursor's dedupe_key>`.
   - At claim time, `claim_queue_jobs()` skips any row whose `depends_on` still matches
     an active-state (`queued`/`retry_wait`/`leased`/`running`) row elsewhere in
     `queue_jobs`. Once the precursor reaches a terminal state (`succeeded`, or
     `failed`/`dead_letter` if that's an acceptable unblock condition — needs a
     decision), it drops out of the active set and the dependent becomes claimable.
   - No new state, no new table — a claim-time `NOT EXISTS` check against the existing
     `dedupe_key`/`state` columns.
   - Cross-queue safe by construction (`dedupe_key` isn't queue-scoped), which matters
     for chains that span different workers (draft → upload → publish are 3 different
     `tgw-worker@` units).
   Related to the still-open #1609 (run-once semantics) but distinct: #1609 is about
   blocking *re*-enqueue after completion; this is about blocking *initial* eligibility
   before a precursor completes.

## Why this shape (not a separate validation layer)

`state_machine` is already this codebase's established name for the whole queue/job
subsystem (the Postgres database is literally named `state_machine`). The manifest
isn't a new abstraction bolted on top — it's `enqueue_job()`'s own input contract,
enforced in the same function, same module. No new service, no new file beyond the
priority-tier config.

**Enforcement lives in our own Python code, not a Claude Code harness hook** — worth
naming explicitly given the same session found `worktree-guard.py`/`app-code-guard.py`
silently non-functional due to an upstream Claude Code bug (todo #1531, invariants.md
E11/E12). `enqueue_job()` validating its own inputs has no such external dependency;
it fires on every call, unconditionally, by construction.

## Sequencing — enforcement cannot switch on until every existing call site is fixed

Per #1607's audit (61 real `enqueue_job()` call sites): 51 already correct, 1 simple
missing-key fix (`ebay_upload.py:186`), 8 workers / 15 call sites need the singleton
`'<queue>:pending'` debounce key (`ebay_legacy_sync`, `ebay_sync`, `token_refresh`,
`velocity_stats`, `ebay_price_reducer`, `ebay_sku_migrate`, `ebay_dole`,
`sync_conflict`), and 3 sites need an explicit decision (`restart-ebay-token`'s force-now
→ resolved by item 4 above; `ebay_sync`'s two per-sku manual-trigger call sites need
their own key distinct from the singleton's; `requeue_job`'s timestamp-suffixed key is
harmless, template-hygiene note only). Order:

1. Fix all 15+ call sites found by the audit (dedupe_key + entity_id, using the
   named-exception pattern for any genuine holdout).
2. Ship `tgw-queue-priorities.json` + the config-lookup default in `enqueue_job()`.
3. Ship `supersede` + the atomic-cancel-then-insert path, wire `restart-ebay-token` to
   use it.
4. Only then flip `enqueue_job()`'s enforcement on (raise on missing dedupe_key /
   missing entity_id for per-item jobs) — flipping it before step 1 lands would break
   `ebay_sync`/`ebay_legacy_sync`'s live production loops immediately.

## Relationship to other PPs

- **PP-SOLD-001** — where the incident chain originated (#1604 data-loss fix, #1605
  worker restore + backlog cleanup, #1607 lease-race root cause). This PP is the
  general infrastructure fix that incident surfaced; PP-SOLD-001 stays the incident
  record.
- **PP-DEADLETTER-001** — related but narrower: that PP triages *why jobs failed* after
  the fact. This PP prevents a whole class of failure (duplicate/malformed job
  issuance) structurally, before a job ever reaches dead-letter.

## Status

**Manifest items 1-4 (`dedupe_key`, `priority`, `entity_id`/`entity_type`, `supersede`)
built, tested, reviewed, merged, and live as of 2026-07-20** (#1608, all 4 phases;
`tgw-queue-priorities.json` deployed). Invariant **E16** written. Still open from that
build: #1609 (run-once semantics + gate-passing observability) and the actual
lease-heartbeat-renewal structural fix (#1607's remaining scope, currently only
mitigated via the `lease_seconds` 300→600s bump).

**Manifest item 5 (`depends_on`) is design-only, not yet built** (captured
2026-07-21) — needs its own todo/packet once Dave names a concrete use case to
build against. Terminal-state unblock question answered, Dave 2026-07-21: only
`succeeded` unblocks a dependent — `failed`/`dead_letter` on the precursor fails
the whole sequence (the dependent stays blocked indefinitely, not auto-cancelled
or auto-skipped). "I think dead letters fail the whole sequence or we have
unexplained orphans." No automatic resurrection/retry-the-sequence mechanism for
now, "if at all" — a stuck-sequence dependent is a finding for `PP-DEADLETTER-001`
triage to surface (same "skip/guard is a finding, not a log line" pattern as
invariant C11), not something the state machine auto-recovers on its own.

**Correction, same conversation — Dave: "don't we already have on in our intake
path?"** Yes, and it's actually a cleaner solution than `depends_on` for the case
it covers: `TGW-Pipeline-Flow.md`'s whole intake/listing chain (`bundle_intake` →
`ai_identify` → `ebay_draft` → `ebay_upload`/`ebay_price` → `ebay_stage` →
`ebay_publish`) already does completion-gated sequencing today — each worker
enqueues the *next* stage only from its own success path. Job N+1 isn't submitted-
then-blocked; it simply doesn't exist until job N succeeds. No claim-time check
needed, no new manifest field needed, for any sequence that's fixed at worker-code
authoring time. **`depends_on` only earns its keep for a narrower case: an ad-hoc
sequence composed at submission time**, across workers that don't already know
about each other in code — e.g. an operator or agent issuing an arbitrary N-step
chain as one batch, not a new pipeline stage being added to existing worker
source. Until a concrete need like that shows up, this stays a named-but-unbuilt
idea, not a gap in the current pipeline.
