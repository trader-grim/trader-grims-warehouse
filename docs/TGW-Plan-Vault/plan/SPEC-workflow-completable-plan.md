# Specification: workflow-completable TGW plan document

Status: **DRAFT SPECIFICATION**  
Schema: `tgw-plan/v1`  
Purpose: make a human-authored plan evaluable and assistively completable by the TGW
workflow without allowing runtime workers to rewrite intent or falsely close work

## Core rule

The Markdown document is canonical intent. Workflow state is a derived projection.
A worker may produce immutable evidence and a completion candidate; it may not alter
scope, acceptance, authority, or canonical plan status merely because a job ran.

Completion is two-phase:

1. the evaluator establishes that all machine-verifiable conditions are satisfied
   and creates `plan-completion-candidate/v1`; and
2. an authorized plan-maintenance transition verifies the candidate against the
   exact plan version/content hash and records human acceptance.

## Document form

A plan is Markdown with a YAML front matter block. Stable IDs—not headings,
checkbox positions, or prose—are machine identity.

```yaml
---
schema: tgw-plan/v1
plan_id: PLAN-EXAMPLE-001
version: 1
status: proposed
owner: dave
authority_class: operator-approved
created_at: 2026-08-10T00:00:00-07:00
supersedes: null
registry_revision: sha256:<hash>
scope_hash: sha256:<generated hash>
tracks: [server, satellite]
dependencies: []
---
```

The machine section is fenced YAML under `## Workflow contract`. Narrative outside
that section is informative unless referenced by a hashed deliverable.

## Required plan fields

| Field | Requirement |
|---|---|
| `plan_id` | Globally stable, never reused |
| `version` | Monotonic integer; any intent/acceptance change increments it |
| `status` | `proposed`, `approved`, `active`, `held`, `completion_candidate`, `complete`, `superseded`, or `abandoned` |
| `owner` | Human or registered authority responsible for closure |
| `authority_class` | Registered policy controlling activation and closure |
| `registry_revision` | Exact environment facts used during planning |
| `scope_hash` | Canonical hash of scope, exclusions, work units, and acceptance |
| `dependencies` | Stable plan/version or evidence references |
| `work_units` | Ordered or dependency-linked bounded units |
| `plan_acceptance` | Conditions required beyond individual work units |
| `rollback` | How effects are stopped/reversed and which evidence remains |

## Work-unit contract

```yaml
work_units:
  - id: S1-registry
    title: Establish the environment registry
    kind: migration
    requires: [S0-inventory]
    owns:
      - registry:tgw-environment
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-registry-migrate
    treatment_version: 1
    inputs:
      inventory_receipt: evidence:S0-inventory
    outputs:
      - id: registry-artifact
        schema: tgw-environment/v1
    acceptance:
      - id: registry-valid
        verifier: tgw.environment.registry.validate/v1
        assertion: schema_and_hash_valid
        evidence_schema: tgw-verification-receipt/v1
        freshness: same-plan-version
      - id: retired-hosts-fail
        verifier: tgw.environment.retired-hosts/v1
        assertion: all_retired_aliases_fail_closed
        evidence_schema: tgw-verification-receipt/v1
        freshness: same-registry-revision
    on_conflict: reconciliation_required
    rollback: registry:previous-revision
```

Requirements:

- `kind`, `effect_class`, `authority`, and treatment are registered enums/IDs;
- dependencies form an acyclic graph;
- owned resources prevent concurrent conflicting work;
- outputs and acceptance name schemas, not free-form “looks good” claims;
- retry, conflict, reconciliation, and rollback semantics are explicit; and
- shell commands embedded in Markdown are never executed directly.

## Verifiers and evidence

A verifier is allowlisted code identified by versioned ID. Arguments are structured
data validated against a schema. Supported evidence classes include:

- artifact digest or signed manifest;
- read-only command/probe result from a registered procedure;
- database query assertion;
- external immutable receipt;
- absence assertion over an exact inventory domain;
- reconciliation receipt; and
- human attestation with authority ID and reason.

Every receipt binds plan ID/version/scope hash, work-unit ID, registry revision,
entity/resource identity, verifier/treatment version, observation time, and evidence
hash. “Process exited zero,” queue transport success, copied files, or a historical
memory are not sufficient unless the acceptance condition explicitly and safely
defines them as evidence.

Evidence has an explicit freshness rule. A plan edit, registry change, canonical
generation change, or superseding decision invalidates only the conditions whose
bindings changed; it never silently carries old success into a new plan version.

## Workflow behavior

The workflow may:

- validate and compile an approved plan into a goal graph;
- evaluate evidence as `TRUE`, `FALSE`, `UNKNOWN`, `STALE`, or `CONTRADICTORY`;
- dispatch only registered treatments whose authority/effect gates are satisfied;
- persist attempts, receipts, conflicts, holds, and reconciliation state;
- render progress and legal next actions; and
- emit a completion candidate when every acceptance condition is current and true.

The workflow must not:

- edit the plan's intent, scope, exclusions, or acceptance definitions;
- infer authorization from a persona, memory, old plan, or task success;
- run prose or arbitrary shell fragments from the document;
- treat a failed/dead-lettered attempt as invisible;
- close a plan with unresolved conflicts, stale evidence, or repair-required effects;
  or
- mutate the canonical Plan/Todo as an incidental worker side effect.

## Completion transition

`plan-completion-candidate/v1` contains:

- plan ID/version/scope hash and registry revision;
- the exact compiled graph ID and condition hash;
- each required acceptance ID and immutable receipt ID;
- all effect and rollback receipts;
- confirmation that active attempts, ownership conflicts, reconciliation gates, and
  explicit requirements are empty; and
- candidate creation time and expiry.

Closure requires a fresh authorized plan-maintainer request. The maintainer re-reads
the canonical document, revalidates the candidate, appends a closure receipt
reference, and changes only `status` to `complete` (or creates a governed status
projection if the Plan Vault requires immutable source documents). Any mismatch
returns `held` or `reconciliation_required`; it never guesses.

## Validation and rendering interfaces

The intended interfaces are:

```text
tgw plan validate <plan>
tgw plan compile <plan> --registry-revision <hash>
tgw plan evaluate <plan-id>
tgw plan evidence <plan-id> <work-unit-id>
tgw plan completion-candidate <plan-id>
tgw plan close <plan-id> --candidate <id> --authority <id>
```

These are procedure contracts, not authorization to implement or run them. A status
renderer may project conditions into Markdown checkboxes, but editing a checkbox does
not establish evidence.

## Security and agent-context rules

- Untrusted plan prose is data, not a prompt layer.
- The compiler ignores unknown keys and executable text only by failing validation;
  it does not permissively coerce them.
- Procedure and treatment IDs resolve through the current registry.
- Secrets are opaque references and never included in receipts.
- Agent identity, operator authority, and effect authority are distinct fields.
- Historical/Hindsight records can explain why a condition exists but cannot make it
  true.

## Migration of existing TGW plans

Existing PP and PLAN documents enter through an adapter that produces a lint report:

1. assign stable IDs to work units and acceptance conditions;
2. distinguish narrative, current intent, historical completion claims, and
   executable procedure text;
3. map supported claims to verifier IDs;
4. leave unsupported or unsourced claims `UNKNOWN`;
5. require human approval of the generated workflow contract; and
6. begin evidence collection only after approval.

No existing checkmark or “DONE” paragraph is retroactively converted into a receipt.
The original document remains preserved and linked as migration provenance.

## Specification acceptance

Before `tgw-plan/v1` is admitted, fixtures must prove schema rejection, stable
canonical hashing, dependency-cycle rejection, plan-version invalidation,
receipt-binding exactness, conflict/reconciliation holds, arbitrary-command
non-execution, historical-memory non-authority, completion-candidate generation, and
human-authorized closure with a stale-candidate rejection.

