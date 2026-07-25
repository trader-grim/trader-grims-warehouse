# TIGWA PLAN — PP-SELLERHUB-001 evidence-first audit execution

**Owner:** Tigwa
**Review:** Claude, then Dave acceptance
**Status:** Execution plan; collection/model spend remains gated
**Inputs reviewed:** `PP-SELLERHUB-001.md`; Claude’s `docs/ai-plans/shcs-phase0-audit-scoping.md`; linked #1513, Radar, Catio, librarian, Postgres, and operational-reality requirements.

## Objective

Build the durable Seller Hub Capability Specification (SHCS) that answers, with account-specific evidence:

1. what the actual Seller Hub offers;
2. what TGW actually does today;
3. what is full parity, partial, absent, guessed, intentionally deferred, or unverified;
4. where TGW should deliberately surpass Seller Hub through Dave-conceived tools; and
5. what is next, by risk, dependency, owner, and acceptance gate.

This is a read-only evidence and planning project. It does not mutate eBay, configure credentials, change TGW code, or promote an audit row into implementation without review.

## Chosen model/tool routing

**Primary audit analyst: direct Gemini API, `gemini-2.5-pro`, pinned by exact version at run start.**

Why: this audit needs one model to reason over a large, multimodal, curated corpus — Seller Hub screenshots and UI notes, official eBay documents, API responses when the future read-only seam exists, code/route maps, existing PP/todo history, and the evolving SHCS register. Gemini’s official documentation identifies 2.5 Pro as its advanced complex-task model; its long-context tooling supports 1M+ token workflows, multimodal inputs, structured output, and context caching. It is the right synthesis/review tool, not the source of truth.

**Do not use generic Search grounding as the audit authority.** Search can find current official eBay documentation and surface candidates, but every material claim must retain its own first-party source URL or captured account evidence. A past Gemini grounding result was insufficiently retained; this plan corrects that by preserving raw evidence and provenance first.

**Supporting tools:**
- Tigwa browser/computer-use capture in a dedicated Seller Hub session for UI evidence; screenshots/notes are raw account evidence.
- TGW read-only codebase analysis for routes, workers, API boundaries, tests, and runtime probes.
- Direct eBay read-only API evidence only after #1513’s least-privilege connector contract is reviewed; never token telemetry, token files, or a write-capable credential.
- DeepSeek V4 Flash only for bounded extraction/normalization/deduplication after the raw evidence is stored; never to establish parity or make a disposition.

No Gemini run occurs until the precise model/version, input/output/caching estimate, retention/logging setting, and quota/cost ceiling are recorded and Dave approves that spend. No secret is placed into chat, a plan, or an audit artifact.

## Artifacts and source hierarchy

1. **Raw evidence manifest** — capture ID, timestamp, surface, account scope, source URL/API endpoint, screenshot/file hash, observer, and permitted-use note.
2. **TGW behavior map** — route/UI/worker/API/test/live-probe provenance, with no claim inferred merely from an intended design.
3. **SHCS parity register** — Claude’s approved row schema; `full-parity` structurally requires both real account evidence and traced TGW behavior.
4. **Enhancement register** — separate from parity rows; captures Dave’s better-than-Seller-Hub tools without pretending eBay is the product ceiling.
5. **Evidence-to-decision reconciliation ledger** — discrepancies, risk, PP/todo, proposed disposition, owner, gate, and required closure evidence.
6. **Review packet** — a bounded batch of rows, raw-evidence links, synthesis output, uncertainties, and recommended next collection/build/replan action.

Raw evidence outranks model synthesis. Model output is always derived/reviewable. EBay account evidence and TGW live/code evidence remain separate fields.

## Phased execution

### A0 — control setup and canary (no account mutation)

- Confirm existing-account-only scope; PP-EBAY-ACCOUNT2-001 remains sandbox-only.
- Create the local/Plan-Vault audit artifact layout and register templates; preserve hashes and evidence lifecycle.
- Confirm a dedicated, read-only browser session/profile; no credential copying or browser-cookie export.
- Run a small Gemini canary on synthetic/public non-account material to validate model pin, structured-output schema, logging/retention behavior, cost metering, and evidence-link preservation.
- Review the canary before any account-derived corpus leaves the controlled capture set.

**Exit:** reviewed tool/cost/security envelope; no collection or model synthesis proceeds if it is not acceptable.

### A1 — risk-first UI evidence capture (no model required)

Populate raw SHCS evidence through direct Seller Hub inspection, in this order:

1. business policies: shipping, payment, returns; policy selectors/defaults; account-backed lists;
2. listing lifecycle and high-risk listing controls: condition, categories, item specifics, price, offers/Best Offer, publishing, bulk/exception controls;
3. order, fulfillment, cancellation, returns and recovery paths;
4. marketing/promotions and finances/reports;
5. messages, store, profile/settings.

For every observed control/list: capture its visible behavior, context/feature flag where relevant, source URL, stable identifier/count/page behavior if visible, timestamp, and screenshot/DOM evidence reference. Do not click mutation controls.

**Exit per surface:** evidence completeness is recorded honestly; inaccessible/ambiguous controls are `blocked-unverified`, never guessed.

### A2 — TGW behavior mapping in matching batches (no model required)

For the same surface batch, trace TGW web, API, worker, CLI, Flutter/mobile, config/default, and test seams. Record code and test paths, live read-only probe when appropriate, list source/freshness behavior, and failure state.

Start with existing regression rows (#895, #12, #1631, Best Offer constraint) to prove the procedure catches known history.

**Exit:** each collected Seller Hub row has a corresponding TGW map or an explicit absence/unverified state.

### A3 — Gemini-assisted synthesis (costed, review-gated)

For each bounded surface batch, send Gemini only the curated evidence bundle and schema. Prompt it to:

- propose normalized capability IDs and candidate status/gap/risk classifications;
- identify conflicts, duplicates, missing evidence, and ungrounded claims;
- separate parity observations from enhancement candidates;
- return strict structured output with source capture IDs, never prose-only conclusions.

The prompt asks its question after the evidence context, consistent with Google’s long-context guidance. Use context caching only after the canary proves that reuse, retention, and cost are acceptable.

Tigwa validates every proposed `full-parity`, `closed`, high-risk, and build-now row against raw evidence. A valid JSON response is not a valid audit result.

**Exit:** staged rows have complete provenance, human review status, and no model-only factual claim.

### A4 — reconciliation and sequencing

Reconcile SHCS with PP-SELLERHUB-001, PP-EDITOR-001, PP-RADAR-001, #1513, PP-POSTGRES-001, and the operational-reality register. Classify each material gap as parity, implementation, documentation/runbook, test/monitor/recovery, authority/provenance, intentional divergence, or enhancement.

Produce capacity-lane recommendations:

- critical/revenue/compliance fixes;
- product/harness build candidates;
- read-only connector/data-product prerequisites;
- longer-term enhancement and Postgres-read-model candidates;
- deferred/not-needed items with rationale.

Only Dave/review acceptance promotes a row into a canonical plan or task.

## Acceptance criteria for the first audit release

- Every high-risk policy/listing/fulfillment control has a SHCS row or an explicit evidence blocker.
- Every claimed TGW parity row names real account evidence and actual TGW behavior; lists/defaults state authoritative source and freshness behavior.
- Best Offer remains a visible operator control; no silent inference/default can claim parity.
- Each row has PP/workstream, owner, risk, evidence lifecycle, dependency, and next review gate.
- Enhancement ideas are retained separately and not silently converted to parity claims or implementation tasks.
- A review packet identifies the top evidence-backed next work, not merely a long feature list.

## Needed from Dave before paid/model-assisted work

1. Confirm `gemini-2.5-pro` direct API as the primary audit analyst, or name another preferred Gemini model.
2. Approve a capped, recorded Gemini canary and later per-surface synthesis budget after the exact quote/version is captured.
3. Confirm the A1 risk order (policies/listing controls first) and existing-account-only capture boundary.

Until then, A1/A2 can begin with direct read-only UI/code evidence; A3 remains stopped.
