# SHCS Phase 0: Seller Hub Capability Specification — audit scoping packet

**Status:** Draft — 2026-07-22, in response to Tigwa's REQUEST/ADDENDUM/CLARIFICATION (folded into `PP-SELLERHUB-001`).
**PP ref:** PP-SELLERHUB-001 (primary), PP-EBAY-ACCOUNT2-001 (sandbox), integration with PP-RADAR-001, PP-HERMES-EA-001/PP-CATIONIX-001, PP-POSTGRES-001, #1513.
**Authority:** Design/review only. No eBay mutation, credential handling, model-spend commitment, or UI implementation.

## 1. Register schema

One row per Seller Hub capability/control. Columns:

| Column | Meaning |
|---|---|
| `capability_id` | stable slug, e.g. `sh.policies.shipping.flat_rate` |
| `sh_surface` | inventory/listings, orders/fulfillment/returns, messages, marketing/promotions, finances/reports, store/categories, business policies/settings, bulk/exception workflows, context-dependent controls |
| `sh_evidence` | account evidence: UI screenshot/description or authorized read-only API response, **scoped + timestamped** |
| `tgw_behavior` | TGW's actual web/API/worker/mobile behavior, with code path (`file:line`) or runtime evidence |
| `list_source` | for any list/dropdown/autocomplete/default: backing source, stable IDs, count/pagination, retrieval/freshness/cache behavior |
| `status` | `full-parity` \| `partial` \| `guessed-local-substitute` \| `read-only-only` \| `absent` \| `intentionally-deferred` \| `blocked-unverified` |
| `gap_type` | operational / revenue / compliance / data-integrity |
| `risk` | low/med/high, tied to gap_type |
| `disposition` | build now / capacity-queued / deferred / not needed |
| `pp_workstream` | PP/todo link (Tigwa's integration requirement #1) |
| `owner` | who's accountable for closing the gap |
| `dependency` | e.g. blocked on #1513 connector |
| `evidence_lifecycle` | collected → synthesized → reviewed → accepted/deferred/superseded — this is not a new invention, it's the same governing constraint already decided in `RESEARCH-all-research-submissions-operator-acceptance-gate-2026-07-20.md` (Dave, 2026-07-20): every submission stages with provenance first, never auto-becomes canonical, and operator acceptance is distinct from review/synthesis/implementation authority. SHCS rows are a research submission under that same gate. |
| `next_review_gate` | when this row gets re-checked |

**Invariant baked into the schema, not just prose:** a row cannot reach `full-parity` without both `sh_evidence` and `tgw_behavior` populated with real provenance — an empty `sh_evidence` field structurally blocks that status value (enforced at review time, not by a code constraint yet).

## 2. Sample rows (from already-known gaps/fixes)

| capability_id | sh_surface | status | gap_type | disposition |
|---|---|---|---|---|
| `sh.policies.shipping.flat_rate` | business policies | was `guessed-local-substitute` → fixed (#895) | data-integrity | closed, keep as regression row |
| `sh.listings.shipping_cost` | inventory/listings | was `absent` (9 wrong-shipping listings, #12) | revenue | closed, keep as regression row |
| `sh.categories.sync` | store/categories | `partial` — TGW has category *data* (PP-CATPICK-001) but no live *management* | operational | capacity-queued |
| `sh.listings.condition_dropdown` | inventory/listings | fixed 2026-07-21 (`DropdownButtonFormField` API break, #1631) | data-integrity | closed, keep as regression row |
| `sh.offers.best_offer` | inventory/listings | explicit invariant: must render as an operator-visible checkbox, never an inferred default | compliance | design constraint, not a gap |

## 3. Required account/UI/API evidence and explicit blockers

- **Blocker, must resolve first**: identify the existing token-facility owner (per CLAUDE.md's single-facility rule, `tgw.apis.secrets.get_api_key`) and define the **least-privilege read-only seam** for this audit — scopes, expiry failure behavior, provenance envelope. Do not start an API connector from token-health telemetry (Tigwa's explicit boundary). This is the same seam #1513 already proposes; this audit becomes the first real consumer.
- Read-only against the *existing* seller account only. Second account (PP-EBAY-ACCOUNT2-001) stays a later sandbox, no registration work here.
- **UI evidence collection is already assigned — not an open question.** Todo #1465 (2026-07-16, Dave via Tigwa, redirected same day): Tigwa runs live Seller Hub inspection herself using her `computer_use` browser-spin-up skill (SOM-mode screenshot capture) plus vision-model capability — she's better suited to this than Claude for live UI inspection, and it keeps credential handling inside her existing boundary rather than routing through a new one. `sh_evidence` rows in the register should be sourced from her, scoped/timestamped at capture time — this SHCS packet is the register her evidence populates, not a new collection assignment.
- API evidence: only through the least-privilege seam once built (§ blocker above) — not a prerequisite to *start*, since Tigwa's UI-only evidence is already valid partial evidence today.

## 4. Risk-ranked initial audit order

1. Wrong-listing / fulfillment / policy / compliance risks (shipping, returns, business policies) — direct revenue/compliance exposure, and the class that already produced two real incidents (#895, #12).
2. Bulk/exception workflows — high blast-radius if a "guessed-local-substitute" silently governs a bulk action.
3. Inventory/listings core surface (condition, categories, item specifics) — high-volume, already has known gap history.
4. Orders/fulfillment/returns.
5. Marketing/promotions, finances/reports — lower immediate risk, defer past first pass.
6. Messages, store/settings — lowest risk, last.

## 5. Acceptance criteria — when is a control "truly authoritative" vs. merely rendered

A row may claim `full-parity` only when: (a) `sh_evidence` is real account evidence, not documentation/assumption; (b) `tgw_behavior` traces to actual code, not intended behavior; (c) any list/dropdown/default names its `list_source` with freshness/cache behavior, not just "looks right in testing"; (d) Best-Offer-class controls remain operator-visible, never silently defaulted. Anything short of all four stays `partial` or `guessed-local-substitute` — this is the exact class of bug invariant C14 (operator correction silently lost) and the eligible-filter incident (#1377) both trace back to.

## 6. Scheduling as a capacity-funded discovery lane

Per Tigwa's three-lane model (already authorized for PP-POSTGRES-001, same shape applies here): this audit runs as its **own bounded discovery lane**, not folded into the critical-integrity or product/harness runways, and not blocking either. Phase 0 (this packet + first evidence-collection pass) is cheap — UI evidence collection + code-path tracing for already-known gaps, no model spend. Model-assisted synthesis (comparing collected evidence at scale) is a **separate, explicitly costed decision** — this packet does not authorize it; per Tigwa's boundary, any spend-bearing run needs its own scope/cost estimate and Dave's approval first.

## 7. Integration matrix (Tigwa's addendum requirement)

| Audit output | Tigwa project/PP | Consumer/owner | Authority boundary | Evidence/acceptance gate | Capacity dependency |
|---|---|---|---|---|---|
| SHCS register (this doc) | PP-SELLERHUB-001 | Dave (sequencing), Claude (build) | design/review only | row-level per §5 | discovery lane, own WIP |
| Read-only connector contract | #1513 | Tigwa (consumer) | no token files, no mutation, no shell | bounded evidence fields only (§3) | blocked on token-facility review |
| Radar surfacing | PP-RADAR-001 | Tigwa/operator UI | audit-only facts stay audit-only unless explicitly marked operator-safe | provenance+freshness required on every operator-visible value | not started |
| Autonomy training data | PP-HERMES-EA-001 / PP-CATIONIX-001 | Tigwa (classify/summarize only) | no create/revise/end/policy-alter, no promotion to canonical plan without human review | bounded review packets | pre-authority-unlock |
| Librarian provenance | #1433/#1434/#1439 | Tigwa (librarian role) | raw evidence and derived conclusions never merged | lifecycle: collected→synthesized→reviewed→accepted/deferred/superseded | ongoing |
| Postgres data-product seam | PP-POSTGRES-001 | future relational read-model | portable export, no Postgres dependency now | reuses P1's data-product-inventory shape | not a prerequisite |

## 8. Dave-conceived enhancements (Tigwa's clarification — separate from the parity register)

A second, explicitly separate table alongside the parity register — same file, different section, never merged row-for-row with parity rows:

| enhancement_id | related_sh_capability | parity_gap | dave_intent | classification | required_inputs | authority_constraint | status |
|---|---|---|---|---|---|---|---|
| e.g. `radar.anticipatory-context` | none required | none required | surface current-item context/actions proactively | new workflow/data product | provenance-tagged item state | operator-visible, no hidden inference | candidate |

Rule (per clarification): an enhancement never needs a Seller Hub equivalent to be valid, and Seller Hub's feature set is not TGW's ceiling — but recording an idea here does not itself authorize building it.

## Open questions for Dave/Tigwa before Phase 0 evidence collection starts

- Confirm PP-EBAY-ACCOUNT2-001 stays fully out of scope for this pass (sandbox only, no registration).
- Confirm the risk-ranked order in §4 before committing collection effort.
