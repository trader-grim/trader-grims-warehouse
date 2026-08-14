# PP-WORKFLOW-001 B0 — privacy-safe reconciliation surface

**Status:** READY FOR INDEPENDENT SOURCE REVIEW; not admitted for release
**Base commit:** `a25c734e853165a807abbf8aa434c8922027b12e`
**Tracked implementation diff SHA-256:**
`fb27f0643d8ae27605aa404480bdeee9f0ff46b93e14da41f9a257c75a943c2f`

## Outcome

Add one authenticated, read-only item endpoint that joins the exact safe columns
needed by the provider-reconciliation runbook:

`GET /api/items/{sku}/workflow-reconciliation`

The response binds configured provider identity and canonical stage/publish marker
IDs to allowlisted rows from `provider_effects`, `operator_authorities`, and
`provider_observations`.

## Exclusions and effects

- No provider call, queue dispatch, retry, reconciliation transition, canonical
  mutation, database write, deployment, or service change.
- No `request_json`, `authority_json`, provider result JSON, token, credential, or
  secret configuration value is selected.
- Effect class: source-local reversible; runtime endpoint is read-only and requires
  the existing API authentication dependency.

## Review requirements

1. Verify every SQL projection against the admitted reconciliation runbook and live
   schema; reject any accidental payload/result selection.
2. Verify exact SKU predicates and bounded ordering for all three ledgers.
3. Verify the route cannot bypass `AUTH` and cannot reach provider or mutation code.
4. Verify canonical marker extraction handles missing or malformed item blocks
   without inventing evidence.
5. Verify configured provider identity comes only from current runtime configuration.
6. Recompute the tracked diff hash from the base commit and compare it before release.

## Acceptance evidence

- Focused ledger/endpoint suite: 65 passed in 1.19s.
- Expanded workflow suite: 236 passed in 1.95s.
- HTTP server plus Action Card suite: 365 passed in 4.92s.
- Ruff, Python compilation, and `git diff --check`: passed.
- Still required: independent review receipt; immutable release identity; production
  route probe for both controlled SKUs; confirmation that the route performed zero
  writes/provider calls; B0 verifier receipt after contradictions are reconciled.

## Rollback

Remove the route and helper from the candidate source or select the prior immutable
release. The endpoint creates no runtime state, so ledger and canonical records are
left untouched. Preserve review, test, and reconciliation observations.
