# TIGWA ADDENDUM — Portfolio integration requirements for Seller Hub audit

**From:** Tigwa
**To:** Claude
**Re:** `TIGWA-REQUEST-2026-07-22-prioritize-sellerhub-full-parity-audit-scoping.md`
**Status:** Review/sequence requirements only; no additional implementation authority.

Dave asked that Tigwa’s broader project responsibilities be represented in the Seller Hub audit rather than treating it as an isolated feature inventory. The SHCS/scoping packet should therefore include these integration requirements.

## 1. Tigwa review and sequencing seam

The audit must be consumable as a capacity map, not a prose report. Give every register row a PP/workstream, owner, evidence state, risk, dependency, suggested lane, and next review gate. This lets Tigwa answer “what is next?” from verified gaps while preserving Dave’s authority over sequencing.

No claim may become “complete” from a Claude implementation note alone. Each accepted row needs source evidence, TGW implementation evidence, an explicit acceptance test/result, and a freshness/next-revalidation condition.

## 2. Librarian / PM-intake / evidence provenance

Tigwa’s librarian responsibilities (#1433/#1434/#1439) require raw evidence and derived conclusions to remain separate. Preserve UI observations, API evidence, account/listing context, timestamps, source URLs/endpoints, stable IDs/counts, and model route/version (if any) alongside each SHCS conclusion. The SHCS is a derived, review-gated register; it must not silently replace raw account evidence or canonical plan decisions.

Audit outputs need an explicit lifecycle: collected → synthesized → reviewed → accepted/deferred/superseded. Mailbox delivery acknowledges communication, not acceptance or platform truth.

## 3. Read-only connector and authority boundary

The parity audit is the primary consumer for #1513’s proposed Tigwa read-only Seller Hub connector. Its contract needs to produce bounded non-secret evidence: account scope, retrieval time, source endpoint/page, stable IDs, pagination/counts, freshness, and a clear unavailable/stale result. It must not receive token files, refresh authority, marketplace mutation, generic shell, or a hidden wide credential path.

Keep #1459’s least-privilege `tigwa-observe` boundary independent: a useful audit cannot be backed by the prohibited general remote shell/sudo-equivalent route.

## 4. Radar and operator data products

PP-RADAR-001 should be a named downstream consumer, not a later rediscovery. Mark which Seller Hub facts can safely surface in a current-item view (e.g. linked listing state, policy provenance, warnings, current authoritative choices) and which must remain audit-only. Every operator-visible value requires provenance/freshness and a fail-visible unavailable state.

This makes the audit produce useful immediate operator context while avoiding the mistake of presenting a cached/local choice as live Seller Hub fact.

## 5. Catio / controlled autonomy

PP-CATIONIX-001 and PP-HERMES-EA-001 require training before authority unlock. Keep audit collection/read-only comparison deterministic or human-reviewed; a model may classify and summarize candidates, but cannot create/revise/end listings, alter policies, or promote a gap into a canonical task/plan decision. The audit should yield bounded review packets and measurable acceptance criteria suitable for later supervised tool use.

## 6. Postgres migration and archival compatibility

PP-POSTGRES-001’s capacity lane benefits from the same data-product inventory and stable-ID/field semantics. Design the SHCS register so it can later be imported into a relational evidence/read-model without making Postgres a prerequisite now. Preserve a portable, human-readable export and raw evidence links; do not let the Seller Hub audit invent a second source of truth for item state.

## Required output addition

Add an **integration matrix** to the requested audit/scoping packet:

| Audit output | Tigwa project/PP | Consumer/owner | Authority boundary | Evidence/acceptance gate | Capacity dependency |
|---|---|---|---|---|---|

At minimum include PP-SELLERHUB-001, #1513 connector, PP-RADAR-001, PP-HERMES-EA-001/PP-CATIONIX-001, librarian/PM responsibilities #1433/#1434/#1439, and PP-POSTGRES-001.

This is how the audit becomes a reusable gap trove and sequencing input, rather than a one-time report.
