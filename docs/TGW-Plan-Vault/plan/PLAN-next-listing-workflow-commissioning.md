# Next work: listing workflow commissioning

**Recorded:** 2026-08-11
**Status:** READY TO BEGIN — no implementation or production action authorized by this note

## Purpose

Use the next important TGW work—not a synthetic exercise—to continue commissioning the standalone workflow. The accepted result remains governed by each PP's frozen outcome and evidence. Necessary workflow tuning may occur inline; unsuccessful interim attempts are history, not acceptance.

## PP pair

1. **PP-WORKFLOW-001 — governed listing backend**
   - Finish the durable item sequence: identify → draft → price → upload → stage → publish.
   - Replace remaining hard-coded sequencing with evidence-bound evaluation, retries, reconciliation, and operator/provider authority.
   - Never report a stage complete until its declared evidence is durable and verified.

2. **PP-UIPIPE-001 — operator-facing listing workflow**
   - Expose the authoritative item/workflow state and the next legal action.
   - Restore a usable `ai_identify` path and truthful held/error/recovery states.
   - Let Dave recover a stuck item without manual database repair or hidden retry ordering.

The backend contract is frozen first. The UI contract is then frozen against it. Implementation may overlap only where the dependency and ownership are explicit.

## How tuning is handled

- A workflow defect that blocks correct PP execution becomes bounded enabling work inside the active run.
- Fixes require their own tests/evidence and must preserve the PP's outcome and authority boundaries.
- Non-blocking workflow improvements are recorded and deferred; they do not interrupt useful PP progress.
- Queue success, an interim patch, or a provider response is never PP completion.
- The PP completes only when its full acceptance contract passes.

## First session after re-entry

1. Read this note and the current `PP-WORKFLOW-001` and `PP-UIPIPE-001` sources.
2. Reconcile them against the admitted listing-pipeline implementation and current production evidence; do not trust historical status prose.
3. Produce two small frozen contracts with:
   - exact outcome and exclusions;
   - work units and dependencies;
   - authority/effect classification;
   - acceptance evidence and rollback;
   - named controlled test SKUs.
4. Compile/admit `PP-WORKFLOW-001` first and start its highest-value incomplete work unit.
5. Monitor continuously; tune the workflow only when needed to carry the real PP correctly.

## Controlled acceptance context

- `tgw202507261628068`: photos are now synchronized but the interface could not run `ai_identify`; useful for the identify/operator-path acceptance case after current state is freshly verified.
- `tgw202604300922410`: previously supplied as a staged-item candidate; its current canonical and provider state must be freshly verified before use.
- eBay provider identity previously supplied: `winchestermysterykitchen`; runtime configuration remains authoritative.

These identifiers are test candidates, not standing permission for provider effects. Any stage/publish call still requires the frozen PP's exact authority gate.

## Restart prompt

> Begin `PLAN-next-listing-workflow-commissioning.md`: reconcile and freeze PP-WORKFLOW-001 and PP-UIPIPE-001, compile the backend PP first, then execute and monitor it through evidence-backed acceptance while tuning only genuine workflow blockers.
