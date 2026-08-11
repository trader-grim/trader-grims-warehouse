# PP-WORKFLOW-001 — provider-effect ambiguity and reconciliation

**Owner:** shared

**Last verified:** 2026-08-10

**Applies to:** governed eBay stage, publish, targeted sync, and legacy-stage observation

**Last drill:** 2026-08-10, controlled stage/publish and read-only reconciliation acceptance

## Non-negotiable rule

`dispatched`, `ambiguous`, or `reconciliation_required` is never ordinary
retry. The provider may already have accepted the effect. Do not call stage,
publish, upload, repush, or a generic dead-letter requeue until exact read-only
reconciliation proves the outcome.

## Identify the effect

```bash
psql -U tgw state_machine -x -c "
  SELECT effect_id, provider, operation, entity_type, entity_id,
         object_generation, graph_id, treatment_id, treatment_version,
         condition_hash, state, error_detail,
         created_at, dispatched_at, finished_at, updated_at
  FROM provider_effects
  WHERE entity_type='item' AND entity_id='<SKU>'
  ORDER BY created_at DESC;"
```

Do not paste `request_json`, `authority_json`, tokens, or credentials into
incident channels. Record their hashes/IDs and inspect sensitive values only in
the authorized environment.

Confirm the canonical markers:

- stage: `ebay_offer.provider_effect_id`, `offer_id`,
  `stage_content_identity`;
- publish: `ebay_listing.provider_effect_id`, listing/offer IDs, published time;
- legacy read-only onboarding: `ebay_offer.legacy_stage_observation_id` and
  `stage_content_identity`—never a fabricated provider effect.

Dual provider-effect and legacy-observation markers are invalid and must remain
UNKNOWN/gated.

## State handling

| Durable state | Meaning and action |
|---|---|
| `reserved` | No provider call is proven. Resolve why dispatch did not start; do not create a second reservation. |
| `dispatched` | Call may have occurred. Read-only reconciliation required. |
| `succeeded` | Reuse the immutable stored result; repair canonical/projection state without a second POST. |
| `rejected` | Definitive rejection. Preserve it; correct evidence/request before a new graph/authority. |
| `ambiguous` | Outcome unknown. Read-only provider lookup or operator attention; never resend. |
| `reconciliation_required` | Evidence conflicts or is insufficient. Resolve explicitly. |

The unresolved unique fence spans generations for the same provider,
operation, entity type, and entity. A newer item generation must not bypass an
older ambiguous effect.

## Authority checks

Provider writes require an immutable operator-authority row bound to operator,
surface, SKU, goal/version, object generation, pre-authority condition hash,
content identity, configured provider identity, exact scopes, issue/expiry, and
supersession state.

```bash
psql -U tgw state_machine -x -c "
  SELECT authority_id, operator_identity, surface, entity_id,
         goal_profile_id, goal_profile_version, object_generation,
         provider_identity, scopes, issued_at, expires_at,
         superseded_at, superseded_by
  FROM operator_authorities
  WHERE entity_id='<SKU>' ORDER BY issued_at DESC;"
```

Never edit, extend, unsupersede, or synthesize authority. A content/generation
change requires new authority through an admitted operator surface.

## Read-only observations

Legacy-stage corroboration uses `provider_observations`, not
`provider_effects`. It may establish current staged-content evidence only after
exact offer and inventory GET comparisons. It grants no publish authority.

```bash
psql -U tgw state_machine -x -c "
  SELECT observation_id, observation_type, provider, provider_identity,
         sku, offer_id, object_generation, graph_id, condition_hash,
         content_identity, outcome, observed_at, created_at
  FROM provider_observations
  WHERE sku='<SKU>' ORDER BY created_at DESC;"
```

Only `corroborated` with exact current bindings is authoritative. Contradicted,
indeterminate, mismatched, or forged observations remain gated.

## Targeted post-push sync

Targeted sync is a read-only provider observation followed by one generation-CAS
local projection. Its payload must bind the exact succeeded source effect,
provider identity, source operation, offer, generation, graph, and condition.
Timeout/429/5xx and propagation absence use bounded durable waits. Auth failure,
malformed/multiple offers, contradiction, or exhausted absence becomes a
structured non-success—not a provider write.

Use the privacy-safe mixed-queue inventory before selector changes:

```bash
sudo -u tgw env PYTHONPATH=/opt/TGW/current/src \
  python3 -m tgw.workflow.sync_queue_inventory
```

Never reinterpret a governed payload as legacy on rollback.

## Completion criteria

- Exact provider/effect/authority/entity/generation bindings corroborate.
- Any external lookup was read-only.
- No second provider POST/PUT/PATCH/DELETE occurred.
- Canonical markers and projection receipt match the durable effect/result.
- The Action Card no longer exposes ambiguity, or it truthfully remains gated.
- All original effect, observation, authority, and attempt rows are preserved.
