# PP-WORKFLOW-001 — item, attempt, timer, and projection recovery

**Owner:** shared

**Last verified:** 2026-08-10

**Applies to:** tgw-prod, generation-bound workflow jobs

**Last drill:** 2026-08-10, item-mutation crash/reconciliation and lease-fence acceptance

## Start with authoritative state

```bash
sudo -u tgw tgw get <SKU>

psql -U tgw state_machine -x -c "
  SELECT job_id, queue_name, state, lease_owner, lease_token,
         lease_expires_at, not_before, error_code, error_detail,
         payload_json, created_at, updated_at, finished_at
  FROM queue_jobs
  WHERE entity_type='item' AND entity_id='<SKU>'
  ORDER BY created_at DESC;"
```

Then inspect the authenticated Workflow Action Card. Compare every attempt's
generation/hash with the current card. A stale attempt is history, not a retry
candidate.

## Lost lease or stalled active attempt

Do not change `running`/`leased` rows merely because a process is absent.
Confirm the exact `lease_expires_at`, worker unit state, and job history first.
Queue completion is fenced by job ID, owner, UUID lease token, and unexpired
lease; a same-owner stale token cannot complete.

```bash
psql -U tgw state_machine -c "
  SELECT * FROM queue_job_history
  WHERE job_id='<JOB_ID>' ORDER BY created_at;"
```

Use the admitted lease-recovery path. Do not manually copy a lease token or
force a row to succeeded.

## Durable timers

A valid workflow wait has a future bounded `not_before` and retains exact
treatment/profile/entity/graph/generation/condition and provider-source
bindings. Inspect it; do not wake it early.

```bash
psql -U tgw state_machine -x -c "
  SELECT job_id, queue_name, state, not_before, payload_json
  FROM queue_jobs
  WHERE entity_type='item' AND entity_id='<SKU>'
    AND state='retry_wait'
  ORDER BY not_before;"
```

If canonical evidence changes before expiry, a stale timer may truthfully
conflict. Re-evaluate the current graph; do not edit its payload.

## Item mutation `REPAIR_REQUIRED`

`REPAIR_REQUIRED` means the canonical JSON effect may be present while a
derived projection (normally SQLite) is not proven. It is not permission to
repeat the treatment or overwrite the item.

1. Record the mutation `operation_id`, observed/resulting generation, receipt,
   canonical ItemData hash, and projection error.
2. Confirm the canonical generation exactly matches the receipt's resulting
   generation.
3. Reconcile only that immutable operation through the admitted
   `reconcile_mutation`/worker recovery path.
4. If generation changed, stop with conflict. Never project an older document
   over a newer one.
5. Verify SQLite content equals the canonical JSON. A semantic no-op is success
   only after projection verification.

Mutation journals default beneath the configured `item_mutation_journal_root`
(otherwise the ItemData parent's `var/item-mutations`). Resolve the effective
configuration before looking; do not guess or delete journal directories.

## Failed or partial LOCAL treatment

- Preserve its structured receipt.
- Do not clone/requeue it on the same generation and condition hash.
- Correct the named evidence or canonical conflict.
- Request the goal again; the evaluator will either suppress the same attempt,
  select a successor treatment, or expose an operator/reconciliation gate.

## Dead letters

For legacy unbound rows, the general dead-letter runbook still applies. For a
row with `graph_id`, `object_generation`, and `condition_hash`, do **not** use
`tgw dead-letter --requeue`. Preserve it and re-evaluate the item.

For structured `TreatmentFailure`, the durable queue result is evidence. Read
`payload_json->'result'`; do not reduce it to `error_detail` text.

## Completion criteria

- The canonical generation and Action Card agree.
- Projection verification passes or an explicit repair gate remains.
- No active duplicate exists for the graph/treatment.
- A changed-evidence event, authority, or admitted timer—not manual replay—caused
  any continuation.
- Failure history remains queryable.
