# PP-WORKFLOW-001 — operator overview and triage

**Owner:** shared

**Last verified:** 2026-08-10

**Applies to:** tgw-prod, NixOS, PP-WORKFLOW-001 releases at or after `2c02eb2d`

**Last drill:** 2026-08-10, production listing convergence and immutable-release acceptance

This is the entry point for the condition-derived listing workflow. It
supersedes generic “requeue the failed job” advice for any job carrying a
`graph_id`, `object_generation`, and `condition_hash`.

## Safety rules

1. ItemData JSON is canonical item state. PostgreSQL is canonical work,
   authority, provider-effect, observation, and attempt history.
2. A worker receipt is evidence. It does not grant the next treatment.
3. Never blindly requeue a graph-bound job. Rebuild the graph after changed
   evidence, authority, or an admitted timer event.
4. Never replay `ambiguous` or `reconciliation_required` provider work.
5. Change provider selectors one seam at a time. Never run legacy and governed
   producers for the same effect simultaneously.
6. A dead-letter is attempt history, not a dead item. Preserve it.

## Five-minute read-only triage

```bash
sudo -u tgw tgw health
systemctl list-units 'tgw-worker@*' --all --no-pager
journalctl -u 'tgw-worker@*' --since '-30 minutes' -p warning --no-pager

psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*)
  FROM queue_jobs
  GROUP BY queue_name, state
  ORDER BY queue_name, state;"
```

For one SKU, open its authenticated item page and inspect the **Workflow
Action Card**, or call the authenticated read-only route:

```text
GET /api/items/<SKU>/workflow
```

Record these values before acting:

- `object_generation`, `graph_id`, and `condition_hash`;
- unmet and explicit requirements;
- eligible and waiting treatments;
- active attempts and `not_before`;
- ownership conflicts;
- reconciliation and operator gates;
- provider-contract gates;
- `blind_retry_allowed` (governed cards always report false).

## Exact attempt history for one item

```bash
psql -U tgw state_machine -x -c "
  SELECT job_id, queue_name, state, attempt_count, max_attempts,
         not_before, error_code, error_detail,
         payload_json->>'treatment_id' AS treatment,
         payload_json->>'graph_id' AS graph_id,
         payload_json->>'object_generation' AS object_generation,
         payload_json->>'condition_hash' AS condition_hash,
         payload_json->'result' AS result,
         created_at, updated_at, finished_at
  FROM queue_jobs
  WHERE entity_type='item' AND entity_id='<SKU>'
  ORDER BY created_at DESC;"
```

Do not filter only on `payload_json->>'sku'`: governed jobs use the canonical
`entity_type='item', entity_id=<SKU>` envelope.

## Decision table

| Action Card / receipt state | Operator action |
|---|---|
| Goal satisfied, no unmet/explicit requirements | No action. Do not create work. |
| Eligible LOCAL treatment, no active matching attempt | Request/evaluate the goal through the admitted UI/API. |
| Waiting or explicit UNKNOWN/STALE | Supply the named evidence or wait for its admitted event. Do not replay. |
| `operator_gates` | Perform the named review/authority action; authority must bind the current generation and pre-authority hash. |
| Active matching attempt | Observe it. Do not create a duplicate. |
| Failed/partial/conflict on unchanged graph | Preserve it. Change evidence or resolve the conflict before reevaluation. |
| `TRANSIENT_BACKOFF` | Verify its durable future `not_before`; do not start a sleeping worker or manual timer. |
| `ambiguous` / `reconciliation_required` | Use [provider-effect reconciliation](pp-workflow-001-provider-reconciliation.md). Never resend. |
| Item mutation `REPAIR_REQUIRED` | Use [item and projection recovery](pp-workflow-001-item-recovery.md). |

## Escalation evidence

Capture the Action Card JSON, canonical ItemData generation, relevant queue
rows, immutable receipt/result, provider effect or observation ID, selector
values, release generation, and worker journal excerpt. Do not copy secrets,
OAuth tokens, or complete provider payloads into tickets.

## Related runbooks

- [Deployment, selector rollout, and rollback](pp-workflow-001-rollout.md)
- [Item, attempt, timer, and projection recovery](pp-workflow-001-item-recovery.md)
- [Provider-effect ambiguity and reconciliation](pp-workflow-001-provider-reconciliation.md)
- [Production acceptance checklist](pp-workflow-001-acceptance.md)
