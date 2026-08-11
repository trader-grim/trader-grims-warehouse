# PP-ITEM-MUTATION-001 — JSON-authoritative item mutation, receipts, projections, and reconciliation

**Opened and approved for bounded planning/Phase 1 non-production preparation:** 2026-08-03 by Dave Buko through Todo #1714, option 1.  
**Status:** APPROVED ARCHITECTURE / PLAN MATERIALIZED / PHASE 1 NON-PRODUCTION PACKET AUTHORIZED FOR PREPARATION.  
**Not authorized:** commit, source admission, merge/push, deployment, production database or ItemData mutation, queue/provider effects, service changes, Nix/flake changes, Phase 2, or dependent #1711/#1678 execution.

## Decision and ownership

This PP is the canonical current-phase home for Todo #1714's logical item-mutation contract. It owns:

1. JSON-authoritative, presence-aware compare-and-swap (CAS) for one logical item operation;
2. durable operation identity and terminal receipts;
3. truthful synchronous projection outcome, initially SQLite and location/tree projections;
4. durable `REPAIR_REQUIRED` state and idempotent reconciliation after canonical publication;
5. exact append/history preservation and operation replay/conflict semantics;
6. the versioned all-writer manifest and bypass-closure acceptance gate.

This ownership is deliberately separate from:

- **PP-STATEMACHINE-001**, which owns queue-job manifests, claim/lease behavior, and queue sequencing—not item-data transaction authority;
- **PP-WORKFLOW-001**, which owns condition/fingerprint evaluation, goal profiles, treatment eligibility, derived runtime work graphs, and one-shot no-wait pipeline convergence. It consumes this PP's generation/CAS boundary for local item treatments but does not acquire item-mutation authority from evaluation;
- **PP-CATALOG-INCR-001**, which establishes current-phase JSON truth, synchronous per-SKU SQLite projection, and hourly full-rebuild reconciliation;
- **PP-POSTGRES-001**, which may later invert item authority only through its separately gated migration/cutover phases. This PP does not accelerate or imply that inversion;
- provider/eBay reservation, sold/refund policy, and external-effect semantics, which remain in their governing PPs/fixes.

## Current authority and store roles

For this PP's current phase:

- ItemData JSON is authoritative.
- ItemArchive bytes are recovery/provenance evidence, not a competing mutable authority.
- SQLite is a synchronous derived per-SKU projection.
- Location in JSON is authoritative; the location tree is a derived projection.
- Price and designated histories are append-only logical evidence within authoritative JSON.
- NATS ItemData mutation records are an audit/event log, not the state master.
- Catalogs, indexes, thumbnails, and full rebuilds are derived outputs.
- Queue jobs and deferred consumers are downstream effects and do not define item truth.
- Derived eligibility, fingerprints, and runtime worklists are rebuildable PP-WORKFLOW-001 projections bound to the exact item generation; they are not inherited item authority and do not mutate canonical Plan/Todo.

## Ratified transaction contract

### 1. Operations and caller convergence

Create, set/merge/delete, designated append, and identity rename are semantic operations. HTTP, CLI, workers, imports, bulk members, scripts, repair tools, and provider-derived local writers must converge on one private item-mutation boundary. Bulk operations may be partial across SKUs, but every per-SKU result must be truthful.

### 2. Generation and CAS

Existing-item operations bind to a presence-aware full canonical generation. Validation, conditional decisions, before-values, transform, and CAS are evaluated from the locked generation. The initial generation token is an exact-byte SHA-256 of canonical JSON bytes, with an explicit absent-item token for create. A stale CAS returns conflict before archive, canonical, projection, audit, rebuild, queue, or provider effects.

Missing and present-with-JSON-null are distinct. Recursive JSON concrete types are exact: boolean, integer, and float do not alias.

### 3. Serialization

A cross-process per-item exclusive lock covers read through required synchronous projection outcome and durable terminal receipt. Rename locks old and new identities in deterministic order. Global SQLite/location rebuilds must coordinate with item writers. Disjoint SKUs remain concurrent.

### 4. Operation identity and replay

Every operation carries a stable operation ID bound to SKU identity, operation kind, expected generation, and exact JSON-native semantic payload. Exact replay returns the prior durable result without repeating effects. Reuse of an operation ID with a different SKU, operation, generation, or payload is a conflict.

Create validates operation, identity, path containment, JSON-native shape, and ancestors before item-store effects. Concurrent create has one winner.

### 5. Append-only evidence

Price/history append occurs against the latest locked generation and deduplicates by exact JSON-native event identity. A migrated caller must not replace an entire history list from a stale read. Append receipts preserve event identity and the committed generation.

### 6. Canonical commit, projections, and terminal states

The terminal model is:

- `ABORTED`: validation or pre-publication failure; no authoritative or downstream effect.
- `CONFLICT`: stale CAS, operation-ID reuse mismatch, or create/rename conflict; no authoritative or downstream effect.
- `COMMITTED`: canonical JSON and all required synchronous projections match the committed generation, with a durable terminal receipt.
- `REPAIR_REQUIRED`: canonical JSON was durably published but one or more required projections or terminal publications are incomplete or contradictory. The canonical generation remains authoritative; the system persists operation/item/generation-bound repair evidence and returns a truthful non-success result.

After canonical publication, do not roll back by replacing the whole document: that could erase a newer winning generation. Recovery is forward, idempotent reconciliation against the committed generation.

### 7. Crash and reconciliation contract

Durable intent is established before the first authoritative effect. A fresh process must discover and reconcile unfinished operations after death at every durable boundary: intent, archive, canonical publication, SQLite projection, each location/tree boundary, and terminal receipt.

Reconciliation:

- is bound to operation ID, item identity, expected/committed generation, and required projection set;
- verifies persisted content, not only status markers;
- never overwrites a newer canonical generation;
- is idempotent when run repeatedly;
- appends attempt/resolution evidence rather than rewriting historical receipts;
- yields `COMMITTED`, remains `REPAIR_REQUIRED`, or records an explicit conflict requiring separate authority.

### 8. Presence/type-exact receipts

Receipts describe actual persisted canonical bytes and exact projection outcomes. Before/after values use explicit presence envelopes so absent differs from null. No-op, replay, committed, repair-required, aborted, and conflict results remain type-exact and contradiction-free.

### 9. Deferred and external effects

Audit, thumbnail/index work, rebuild triggers, and queue jobs may be emitted only from a committed or explicitly repair-required identity, carry operation/generation provenance, and never cause the item transaction to be reported as ordinary success when required synchronous projections failed. Consumers must reject stale generations before lifecycle or provider effects.

This PP does not itself authorize any provider write. Provider reservation, ambiguous-response handling, and external reconciliation remain separately owned and gated.

### 10. Bypass closure

Use one private canonical-write leaf, a versioned writer manifest, path-aware static enforcement, and representative behavioral probes across direct, HTTP, CLI, worker, import, bulk, rename, append, and rebuild paths. A central module is not sufficient while any reachable writer or stale whole-history replacement bypass remains.

## Phase plan and gates

### Phase 0 — exact packet and deterministic RED preparation

Status: authorized for preparation.

- Freeze current clean source identity and complete tracked/untracked manifest.
- Revalidate the current 71-row writer inventory against current source; preserve additions/removals and false positives explicitly.
- Define exact Phase 1 allowed paths and public compatibility seams.
- Build deterministic pre-edit REDs for CAS conflict-before-effects, operation replay/mismatch, disjoint updates, concurrent create, missing-vs-null/type exactness, post-canonical projection failure, fresh-process reconciliation, and projection content verification.
- Freeze receiver-native worker packet, controller contract, independent review contract, and held effects.

Phase 0 changes no product/test source and performs no production effect.

### Phase 1 — non-production transaction foundation

Status: architecture approved; implementation may be issued only through the frozen Phase 0 packet. Phase 1 remains uncommitted and unadmitted.

Smallest coherent vertical slice:

1. private transaction dependency and operation/receipt schema;
2. cross-process per-item lock and deterministic two-identity lock order;
3. exact-byte generation/CAS and JSON-native operation binding;
4. durable intent and terminal receipt journal;
5. canonical JSON publication plus synchronous SQLite projection;
6. location/tree projection through the same truthful terminal contract where the Phase 1 migrated direct wrappers touch location;
7. idempotent fresh-process reconciliation for the covered projections;
8. migration only of the explicitly allowlisted direct `items.py` wrapper class needed to exercise the foundation;
9. phase-scoped writer enforcement and compatibility tests.

Phase 1 acceptance requires deterministic RED-before-edit evidence, focused and adjacent regression GREEN, crash-prefix/fresh-process probes, exact final manifest, controller replay, and independent exact-tree review. A green Phase 1 does not establish repository-wide bypass closure and does not unblock #1711/#1678.

### Phase 2 — caller migration and all-writer closure

Status: **NOT AUTHORIZED.** Requires a new exact decision after Phase 1 review.

Would migrate the remaining HTTP, worker, import, bulk, script/tool, rename, append, rebuild, and specialized writer classes; coordinate global writers; close the versioned manifest; and establish repository-wide bypass enforcement. No Phase 2 worker may be inferred from this Plan decision.

### Later gates

Commit/source admission, merge/push, deployment, production database or ItemData writes, queue/provider effects, service changes, Nix/flake changes, and operator/live acceptance each remain separate gates. PP-POSTGRES authority inversion remains separate.

## Current evidence and supersession

The historical dirty R3A2 candidate (`10c72f2d28bbfbb5cad4beaba9029d26b5055950`, tree `0f92b41d46c5b8a3f1f9f6790c5710bcd2e954ec`) and its 71-row inventory are reusable evidence only. R3A2 ended `NO-VERDICT / SOURCE-MOVED` and `NO-VERDICT / SOURCE-PACKET-GAP`; it must not be repaired, reviewed, admitted, or used as the Phase 1 implementation base.

A clean packet starts from current governed source and re-proves the inventory. The prior absent-versus-null RED is retained as technical evidence and becomes a Phase 1 contract fixture under this ratified presence-aware contract.

## Dependencies

- Todo #1714 is the founding implementation lineage for this PP.
- #1711 and #1678 remain parked until an applicable later gate proves all-writer transaction coverage; Phase 1 alone does not unblock them.
- #1681 may consume the eventual transaction boundary for sold-state local mutation, but refund/provider/listing-reservation/raw-evidence policy remains #1681 scope.
- PP-STATEMACHINE queue work and genuinely unrelated safe lanes remain independent.

## Acceptance and closure

This PP is not complete until:

1. every reachable current writer is classified and either migrated or explicitly excluded with evidence;
2. deterministic concurrency, create/rename, append, presence/type, crash-prefix, projection, global-rebuilder, replay, and bypass tests pass;
3. independent exact-tree review passes;
4. governed source admission and deployment gates, if later authorized, complete;
5. representative non-provider live behavior and reconciliation are verified without contradictory receipts; and
6. Dave performs final operator acceptance under a separately presented live-evaluation card.
