# TIGWA REQUEST — TGW application capability and operational-reality audit scoping

**From:** Tigwa, at Dave’s direction
**To:** Claude
**Programs:** PP-RUNBOOK-001, PP-EDITOR-001, PP-AIOPS-001, PP-CATIONIX-001, PP-POSTGRES-001, PP-SELLERHUB-001
**Status:** Request for a bounded discovery/scoping packet only. No code, service, configuration, runbook, plan, or production-data change is authorized.

## Direction

Apply the same discipline being requested for Seller Hub to TGW itself. We mostly have the capability; the risk is that our apparent surface, source, tests, runbooks, live deployment, and recovery behavior have drifted apart. Outdated runbooks are one visible symptom, not the whole problem.

This is not a documentation-cleanup exercise and not a greenfield rebuild. It is an evidence-backed **TGW Application Capability and Operational-Reality Register**: identify what exists, what is intended, what is actually exercised, what is monitored/recoverable, and what is unknown.

## Required register dimensions

For each material operator workflow, UI/API/CLI capability, worker, background service, data boundary, and runbook procedure, record:

- intended purpose and governing PP/invariant/decision;
- operator entry point and actual code/config/service owner;
- documented/runbook procedure and its last source-backed validation;
- implementation state and source/test provenance;
- deployed/live evidence, version/freshness, and monitoring signal;
- recovery/rollback/runbook evidence and last drill, where applicable;
- capability state: `implemented-and-verified`, `implemented-not-live-verified`, `documented-but-stale`, `live-but-undocumented`, `partial`, `blocked`, `superseded`, or `unknown`;
- risk, dependency, authority boundary, owner, and next evidence/review gate.

Do not let a Markdown “DONE,” a passing unit test, a running service, or an agent completion claim stand in for all the other dimensions.

## Starting evidence of why this is needed

PP-RUNBOOK-001 still identifies eBay-ops recovery/API-responsibility runbooks and the remaining 17-item gap triage as not started, while the thermal runbook is described as done. Separately, Tigwa’s thermal-monitor completion work (#1385) remains active. This is not necessarily a contradiction; it is exactly the state distinction the register must expose: documented policy, implemented monitor behavior, deployed verification, and exercised incident readiness are different claims.

PP-EDITOR-001 likewise carries live workflow defects (wrong shipping policy, published-without-price, incomplete photo upload) that require a defect → root cause → packet map, not simply a UI feature list.

## Scope and sequence

Start with a low-cost Phase 0 inventory/scoping packet. Reuse existing Plan Vault PPs, architecture references, source/test maps, service/worker inventory, current runbooks, known incidents, and prior audit reports. Do not crawl indiscriminately or claim broad live verification without a bounded probe.

Propose a risk-first audit order. Expected early candidates:

1. listing/publish and eBay-facing flows;
2. item mutation/fence/catalog and upcoming Postgres migration seam;
3. order/sold/picklist/fulfillment recovery;
4. worker/queue/NATS/mailbox delivery and observability;
5. backup/restore, archive, and operational runbooks;
6. Tigwa/Radar/agent tooling and authority boundaries.

The result must feed the later sequencing map: critical reliability runway, active product/harness runway, discovery/audit lane, Postgres capacity lane, prerequisites, and acceptance gates.

## Required output

Return a reviewable packet with:

1. proposed register schema and evidence classes;
2. source-of-truth hierarchy and a method for resolving documentation-versus-code-versus-live conflicts without silently rewriting history;
3. an initial inventory method and bounded Phase 0 scope;
4. risk-ranked starting domains and existing PP/todo links;
5. criteria for an executable/rehearsable runbook versus stale documentation;
6. a cadence for post-change verification and periodic revalidation that is silent while healthy and surfaces only meaningful drift;
7. integration with the Seller Hub SHCS, Tigwa review/sequencing role, and later Postgres/read-model work.

No audit result is a build authorization. Each proposed correction remains review-gated and explicitly sequenced.
