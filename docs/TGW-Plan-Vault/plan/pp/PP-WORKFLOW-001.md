# PP-WORKFLOW-001 — condition-derived, evidence-driven pipeline convergence

**Architecture ratified for Plan inclusion:** 2026-08-03 by Dave Buko.
**Status:** APPROVED ARCHITECTURE / IMPLEMENTATION SEQUENCING AND FIXTURE PROOF REQUIRED.
**Not authorized by this Plan edit:** source implementation, queue/schema mutation, deployment, production ItemData or database mutation, provider/eBay effects, automatic dead-letter replay, service/config/package/Nix/flake changes, or canonical Todo mutation.

## Decision

TGW workflows remain native on the existing queue/state-machine substrate, but pipeline movement is no longer governed by an inherited `next_step`, copied eligibility key, or a worker hard-coding the next queue at the end of its success path.

A goal such as `list_item` declares a versioned target fingerprint set, for example `EBAY_LISTABLE`. The evaluator fingerprints the current authoritative record and evidence, compares actual conditions with the target, and materializes a generation-bound runtime work graph containing only unmet requirements. Satisfied requirements are skipped. Existing workers remain bounded treatments; missing remediation, diagnosis, evidence, and reconciliation treatments are added where the treatment registry has gaps.

The pipeline converges by repeating:

```text
goal + current authoritative record/evidence
  -> versioned condition/fingerprint evaluation
  -> legal unmet treatments
  -> generation/effect-bound claim
  -> one bounded job
  -> immutable receipt/evidence
  -> re-evaluation
  -> target matched | operator gate | reconciliation gate | unsupported condition
```

## Core separation

- **Record facts/evidence** describe what is currently known.
- **Conditions/fingerprints** are versioned, evidence-linked derived conclusions.
- **Eligibility** is recomputed from current condition and generation; it is not inherited or directly granted by a worker.
- **Runtime work graph** is a derived per-record projection of unmet goal requirements.
- **Attempt outcome** records one execution and never makes the business record dead.
- **Canonical Plan/Todo** governs intent, ownership, authority, and implementation sequencing. Runtime worklists do not mutate or replace it.

Stages describe actions; conditions describe records; attempts describe execution history.

## Goal and fingerprint contract

Each goal profile declares:

- stable goal/profile ID and version;
- required fingerprints and evidence classes;
- applicability rules;
- human/provider gates;
- terminal success condition;
- explicit unknown/unsupported behavior.

Each evaluated fingerprint records at least:

- record/entity ID and exact source generation;
- evaluator/rule version;
- result: true, false, unknown, stale, contradictory, or not applicable;
- exact evidence references and source class;
- unmet values/reasons;
- freshness and supersession identity.

Provider observation, locally submitted operation history, canonical TGW state, sold/order evidence, and operator/physical observation remain separate evidence classes. One class cannot manufacture another.

A cached fingerprint or eligibility projection is valid only for its exact record generation, evaluator version, goal version, and relevant evidence set. Relevant record/evidence/rule changes invalidate it.

## Treatment/job contract

Every existing or new pipeline job is registered as a treatment declaring:

- required fingerprints;
- conditions it may establish or invalidate;
- authoritative fields/projections/effect boundary it owns;
- evidence it must preserve;
- expected receipt schema;
- idempotency and attempt identity;
- retry/reconciliation policy;
- stop and operator-attention conditions.

Workers do not enqueue what they assume comes next and do not set authoritative `eligible=true` or `next_step`. They emit truthful receipts describing what they observed and changed. The evaluator derives the next legal work from the new evidence.

## One-shot, no-wait execution doctrine

A job runs once against a fresh condition, record generation, and claim; writes one structured outcome; releases its claim; and exits. Jobs do not sleep, poll, hold a lease while awaiting prerequisites, repeatedly requeue themselves, or enter `retry_wait` merely because nothing changed.

Allowed one-shot outcomes include:

- `SATISFIED`;
- `APPLIED`;
- `NOT_ELIGIBLE` with unmet fingerprints;
- `NEEDS_REMEDIATION`;
- `WAITING_FOR_EVIDENCE`;
- `WAITING_FOR_DEPENDENCY`;
- `WAITING_FOR_AUTHORITY`;
- `TRANSIENT_BACKOFF` with durable `not_before`;
- `RECONCILIATION_REQUIRED`;
- `UNSUPPORTED` or `UNKNOWN`.

Waiting is durable record/worklist data, not a running worker. Re-evaluation is event-driven by relevant record/evidence/receipt changes, operator authority, evaluator/goal-version changes, or expiry of an explicit scheduler-owned `not_before` timer. No change means no new attempt.

## Concurrency and conflict prevention

The evaluator prevents logical conflicts by selecting only treatments whose requirements currently match. The trusted state machine prevents execution races by rechecking under the authoritative boundary:

- record generation and condition/fingerprint hash;
- claim/lease token and attempt identity;
- treatment field/projection/effect ownership;
- pending external intent and idempotency identity;
- prerequisites, supersession, and authority.

Independent treatments may run concurrently when their ownership and preservation contracts are disjoint. Overlapping record mutations or external-effect boundaries serialize. The evaluator decides what should run; the state machine proves it is still legal when it runs.

PP-ITEM-MUTATION-001 supplies the JSON-authoritative generation/CAS, receipt, projection, and reconciliation substrate for local item changes. PP-STATEMACHINE-001 owns queue manifests, claims/leases, dispatch, and durable timers. PP-APPROVAL owns human authority. Provider-effect reservations and ambiguous-response reconciliation remain separately governed.

## Stuck, dead-letter, and remediation doctrine

`stuck` and `dead_letter` are observable operational conditions, not terminal business-record destinations.

- A stalled condition is derived from evidence such as lost worker/lease, missing heartbeat, unchanged condition hash, unmet dependency/evidence, or a no-progress loop.
- A dead-lettered attempt remains immutable execution history attached to the record and treatment.
- The record remains evaluable and can expose legal remediation, diagnosis, evidence, reconciliation, or operator-attention actions.
- The same treatment cannot repeat against the same record generation and condition hash without changed evidence or an explicit eligible timer/authority event.
- Bounded attempt budgets escalate to diagnosis/operator attention rather than unbounded retry.
- Possibly committed external effects always enter `RECONCILIATION_REQUIRED`; they never ordinary-retry or auto-resend.

PP-DEADLETTER-001 remains the historical/root-cause and attempt-evidence owner, but blanket bulk requeue and dependency-failure propagation to a terminal business-record state are superseded by condition evaluation and treatment selection.

## Runtime work graph

A runtime work graph is a materialized projection keyed by record, record generation, goal/profile version, evaluator version, and condition hash. It records:

- satisfied requirements;
- currently eligible treatments;
- waiting treatments and unmet fingerprint reasons;
- active claims/attempts;
- reconciliation and operator gates;
- evidence links and freshness;
- next event classes that can trigger re-evaluation.

It may be persisted for visibility and recovery, but it is derived and rebuildable. It is not canonical Plan/Todo authority and must not silently create, close, or rewrite governance Todos.

## Migration and proof sequence

### Phase 0 — source and behavior inventory

1. Freeze the current item-listing pipeline topology and every hard-coded successor enqueue.
2. Inventory existing worker requirements, outputs, record writes, provider effects, waits/retries, dead-letter paths, dedupe keys, and attempt/lease behavior.
3. Map existing jobs to treatment contracts and name missing treatments.
4. Define the first `EBAY_LISTABLE` goal/fingerprint profile from exact current requirements.
5. Preserve current behavior as fixtures before migration.

### Phase 1 — observation-only evaluator

1. Implement pure, versioned fingerprint evaluation over synthetic/frozen records.
2. Materialize a runtime work graph without dispatching jobs or mutating records/queues.
3. Compare its recommendations against the current pipeline on representative satisfied, incomplete, stale, contradictory, remediation, dead-letter, and external-ambiguity fixtures.
4. Require explainable evidence for every fingerprint and treatment recommendation.

### Phase 2 — one-shot local treatment proof

1. Connect a small allowlisted set of non-provider treatments through PP-ITEM-MUTATION-001 generation/CAS.
2. Prove skip-satisfied, unmet-remediation selection, failed-attempt preservation, successful successor remediation, automatic re-evaluation to ready, and no manual requeue.
3. Prove concurrent disjoint treatments and serialized overlapping treatments.
4. Prove no worker sleeps or retry-waits for prerequisites.

### Phase 3 — bounded listing-pipeline migration

Migrate existing hard-coded successor enqueues treatment-by-treatment. Preserve old behavior until each migrated seam has deterministic parity and rollback. Add missing listing requirements/remediation treatments. Provider writes remain held until reservation, ambiguity, idempotency, and reconciliation contracts independently pass.

### Later — operator and production acceptance

Expose per-item goal, fingerprints, runtime work graph, active attempt, waits, evidence, legal mitigations, and operator gates through Action Cards. Proceed to production only through governed source admission, deployment, representative dry/live proof, rollback, and Dave operator acceptance.

## First mandatory fixture

One synthetic record family must prove:

1. `list_item` resolves to a versioned `EBAY_LISTABLE` target;
2. already-satisfied requirements create no work;
3. an invalid condition selects a remediation;
4. the first remediation attempt fails and is preserved separately;
5. no retry-wait/sleep or duplicate same-generation attempt occurs;
6. changed evidence makes a successor remediation eligible;
7. successful repair causes automatic re-evaluation to `READY`;
8. continuation occurs without manual requeue;
9. disjoint work can run concurrently without lost updates;
10. an ambiguous external-effect control refuses automatic retry and emits reconciliation/operator attention.

## Acceptance and closure

The PP is not implementation-complete until:

- the current listing pipeline is completely mapped into goal fingerprints and treatment contracts;
- every inherited eligibility/next-step authority and hard-coded successor enqueue is migrated or explicitly retained with evidence;
- one-shot no-wait behavior is proven for all migrated jobs;
- event invalidation and timer scheduling are durable and idempotent;
- generation/claim/effect fencing and external ambiguity controls pass deterministic tests;
- dead-letter/stalled records remain visible, evaluable, and remediable without replaying ambiguous effects;
- Action Cards expose current condition and required operator action;
- exact-tree controller and independent review pass;
- governed source/deployment/live gates complete; and
- Dave performs final operator acceptance.

## Staged research input — application-definition/package-resolution logic (2026-08-04)

Dave accepted one raw deep-research document into planning **for examination of direct relevance only**:

- `dev-workflow/research/SOURCE-PP-WORKFLOW-001-application-definition-deep-research-dave-2026-08-04.md`
- `dev-workflow/research/RESEARCH-PP-WORKFLOW-001-application-definition-dependency-logic-2026-08-04.md`

Dave supplied the underlying idea to a deep-research system; the resulting document is not Dave's conclusion and is not presumed grounded in TGW. The observed possible connection is the shared dependency need and whether package-management systems—particularly Portage/Luet—offer reusable resolution logic or technique.

Planning must compare candidate concepts against exact TGW sources and classify them as directly applicable, applicable only after TGW-native translation, analogy only, contradicted/superseded, irrelevant, or unresolved. Relevant questions include canonical capability definitions versus receiver-native recipes, dependency closure, alternatives/providers, profiles, blockers/conflicts, installed-state comparison, phase ordering, pretend mode, receipt preservation, invalidation, and resume. The comparison must also identify the non-transferable parts: TGW's evidence classes, authority gates, partial-work reconciliation, ambiguous external effects, Plan/Todo governance, and operator acceptance.

This entry adopts no external schema, package manager, SAT solver, storage model, or generated conclusion. It creates no implementation task and authorizes no source, queue/schema, deployment, provider, production, or Plan/Todo mutation beyond retaining and linking the research for planning examination.
