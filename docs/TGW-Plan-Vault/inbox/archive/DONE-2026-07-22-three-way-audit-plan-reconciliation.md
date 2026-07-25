# TIGWA SEQUENCING CLARIFICATION — Reconcile Seller Hub audit, TGW reality register, and Master Plan

**From:** Tigwa, recording Dave’s direction
**To:** Claude
**Status:** Defines the next review phase after the two bounded audit/scoping packets exist. No plan/code/runbook change is authorized by this note.

## Required next phase

Do not leave the Seller Hub SHCS and the TGW Application Capability and Operational-Reality Register as isolated reports. Once their Phase 0 evidence is reviewable, reconcile **both** against the canonical Master Plan and linked PP detail documents.

This is a three-way evidence review:

1. **Plan/PP:** intended destination, decisions, prerequisites, owner, sequence, acceptance criteria.
2. **Seller Hub SHCS:** external account-authoritative capability floor plus Dave-conceived enhancement opportunities.
3. **TGW reality register:** actual implementation, test, deployment, operational/runbook, monitoring, and recovery state.

## Reconciliation rules

For every material discrepancy, create a durable reconciliation row. Do not silently edit the plan, documentation, or register to make them appear consistent.

Required fields:

- claim/capability and affected PP/workstream;
- all three source references with hashes/timestamps/line or code/service evidence;
- discrepancy type;
- current risk/operator consequence;
- proposed disposition, owner, dependency, and review gate;
- whether it belongs in the critical runway, active product/harness work, discovery/audit lane, or Postgres capacity lane;
- closure evidence required and next revalidation date/trigger.

Classify gaps explicitly, at minimum:

- **plan gap** — intended work/decision/acceptance criterion missing or wrongly sequenced;
- **implementation gap** — plan says it exists but code/service/UI does not meet the claim;
- **external parity gap** — Seller Hub/account reality exposes a missing or wrong TGW capability;
- **documentation/runbook gap** — instructions are stale, incomplete, unsafe, or no longer match reality;
- **test/monitor/recovery gap** — implementation exists but it is not proven in the relevant mode;
- **authority/provenance gap** — source, permissions, freshness, or canonical ownership is unclear;
- **intentional divergence/enhancement** — TGW deliberately differs from eBay or exceeds it, with explicit rationale.

## Filling gaps

Only after Dave and the reviewing actor accept a reconciliation row should it become a sequenced work item. The corrective action may be a code change, plan amendment, runbook change plus drill, test/monitor addition, data/authority decision, or an explicitly accepted deferral. Each correction must link back to the discrepancy row and close only with the promised evidence.

The output is the evidence-backed sequencing map Dave requested: what is true now, what is next, what can run in parallel, what is blocked, and what “complete” objectively means.
