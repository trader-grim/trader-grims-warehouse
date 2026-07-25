# Execution-plan delta — Dave’s 2026-07-25 review

**Status:** staged feedback; requires Claude/Dave review before canonical acceptance
**Source:** Dave’s live plan review, captured via Uh-huh thought mode

## Required changes

1. **Independent reconciliation review:** Claude authored the plan and may naturally favor its existing implementation choices. Add an evidence-first second opinion that does not self-accept Claude’s conclusions. It must review operator workflow, dependencies, recovery, performance, authority, and claimed-versus-usable tools; report confirmed findings, evidence gaps, alternatives, and a Dave-visible decision table.

2. **Portable Catalog Nix change lane:** Add Nix package-manager changes as an explicit PP-PORTABLE-CATALOG-001 execution workflow: intent → host/package/module inventory → reproducibility policy → evaluation/build evidence → Dave/flake-owner review → batchable rebuild/rollback → post-switch verification. No convenience package install or flake change by an agent.

3. **Workflow capability audit:** Priorities, chained workflows, and queues exist conceptually but are barely operational. Audit actual support for priority, prerequisite/blocker, next action, queue construction, bounded execution, observable result/error/retry, and handoff. Identify where manual scavenger-hunting or opaque queues create performance loss. Produce high-leverage enablement packets, not a rewrite.

4. **Alt-text is a full photo-set/operator workflow:** Current report: bad alt text is visible but cannot be corrected; coverage appears limited to one photo per set; operator lacks sufficient data/provenance. Scope an evidence-backed packet for all eligible photo coverage, order/raw preservation, per-photo provenance and status, edit/replace/regenerate/requeue, review state, and eBay payload mapping. Keep direct-Gemini/fallback/cost concerns as part of—not a substitute for—the full workflow.

5. **Bulk listing and UI queue builder:** Before broad throughput, inventory every UI queue and its actual backing state. Build a capability matrix and a minimum operator queue-builder contract: previewable criteria, persisted membership, sample/count, eligibility reasons, cost estimate, dry-run, audit, hold/cancel, authorization, and safe execution gates. Building/reviewing a candidate queue must not itself enqueue/list/mutate eBay.

6. **Five-hour window constraint:** Dave will be present early, then occupied by laptop SSD swap/reinstall after ISO snapshot/data backup. Use the opening period for Dave-required observations and decisions. Remaining work is read-only evidence assembly and packet preparation; do not assume approval during cutover.

## Guardrail

This is a planning/reconciliation delta. It does not authorize code, Nix/flake, queue, eBay, credential, or production mutation.
