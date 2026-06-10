# Runbook: dead-letter triage

**Failure mode:** jobs landing in `dead_letter` state. By design, dead_letter means "a
human must look" — these jobs **never auto-retry** and sit silently until an operator
acts. A pileup means items are stuck at some pipeline stage.

How jobs get here: a job exhausts `max_attempts`, then `classify_dead_letter()`
(`src/tgw/queue/worker_base.py`) string-matches the error against `_TRANSIENT_ERRORS`
(`token is expired`, `no ebay photo urls yet`, `directory not empty`, `readtimeout`,
`lease_expired`, `connectionerror`). A match requeues with backoff; anything else
dead-letters. `HardFailure` exceptions skip retries and dead-letter immediately, with an
error-level notify.

## Symptoms

- `tgw health` reports nonzero dead_letter counts in its per-queue breakdown.
- Error-level entries in `/opt/TGW/var/log/notifications.jsonl`.
- Items not progressing (e.g. identified but never drafted, staged but absent from
  `tgw staged`).
- A burst of dead letters across many queues at once usually means a shared dependency
  broke (token → runbook 1, Postgres → runbook 4, network).

## Likely root causes

| Pattern | Cause | Route |
|---|---|---|
| All in `token_refresh`, or `invalid_grant` | Refresh token dead | [ebay-token-failure.md](ebay-token-failure.md) |
| eBay errorId 25021 / 25002 / 25500 in `error_detail` | Category-specific eBay rejection | [ebay-stage-publish-rejections.md](ebay-stage-publish-rejections.md) |
| `no price yet` | `ebay_price` hasn't priced the item (thin comps?) | below + [pipeline-stall.md](pipeline-stall.md) |
| `already active listing` | Item already live on eBay | `tgw ebay-pull` to sync status |
| `legacy eBay Item#` | Unresolved legacy listing (ISS-008) | `tgw resolve-legacy <sku>` |
| `all photo uploads failed` | Network / EPS outage | requeue `ebay_upload` once network is back |
| `no draft_listing` | Pipeline stalled before ebay_draft | requeue `ai_identify` / `ebay_draft` |
| New/unknown error text | **Phrasing gap**: a genuinely transient error whose wording isn't in `_TRANSIENT_ERRORS` | requeue; consider extending the pattern list |
| HTTP 400, unknown errorId | New unhandled eBay error | inspect full JSON in `error_detail`; add a handler |

The phrasing-gap cause deserves emphasis: because classification is substring matching,
**rewording an error message silently converts transient waits into dead letters** (e.g.
ebay_stage's `'no eBay photo URLs yet'` string is load-bearing — invariant D6).

## Diagnosis

```bash
# 1. List dead letters with transient/permanent verdicts
sudo -u tgw tgw dead-letter                 # all queues, newest first
sudo -u tgw tgw dead-letter --queue ebay_stage --limit 100

# 2. Full error detail for one job
psql -U tgw state_machine -c "
  SELECT job_id, queue_name, payload_json->>'sku' AS sku,
         attempt_count, error_code, error_detail, updated_at
  FROM queue_jobs WHERE state='dead_letter'
  ORDER BY updated_at DESC LIMIT 20;"

# 3. Cluster by error to spot a common cause
psql -U tgw state_machine -c "
  SELECT queue_name, left(error_detail, 80) AS err, count(*)
  FROM queue_jobs WHERE state='dead_letter'
  GROUP BY 1,2 ORDER BY count(*) DESC;"

# 4. History of a specific job (state transitions are audited)
psql -U tgw state_machine -c "
  SELECT * FROM queue_job_history WHERE job_id='<JOB_ID>' ORDER BY created_at;"

# 5. The item itself (ItemData JSON is truth)
sudo -u tgw tgw get <SKU>
```

## Recovery

**Fix the cause first, then requeue.** Requeueing into a still-broken dependency just
burns attempts.

```bash
# Requeue everything whose error classifies as transient (batch, safe):
sudo -u tgw tgw dead-letter --requeue-transient

# Requeue one specific job (clones it WITHOUT a dedupe key — fresh job id):
sudo -u tgw tgw dead-letter --requeue <JOB_ID>

# Drop dead letters that are obsolete (e.g. item since handled manually):
sudo -u tgw tgw dead-letter --cancel <QUEUE_NAME>

# Bulk re-drive items stuck at a known stage (dry-run by default; add --run to execute):
sudo -u tgw tgw requeue --no-price            # preview items missing a price
sudo -u tgw tgw requeue --no-price --run
sudo -u tgw tgw requeue --no-draft --run
sudo -u tgw tgw requeue --unidentified --run
```

Notes:

- `--requeue` / `--requeue-transient` use `requeue_dead_letter_job()`: the old row is
  cancelled and a clone is inserted **without** a dedupe key — this is the only correct
  re-enqueue path (a manual `enqueue_job()` with the old dedupe key would collide).
- Every handler is idempotent (invariant D5), so requeueing an already-completed item is
  a no-op skip, not a duplicate side effect.
- If the same job dead-letters again with the same error, stop requeueing and treat it as
  a code/eBay issue — file it in `reference/ISSUES.md`.

## Rollback

- Requeued the wrong jobs: cancel them before a worker claims them —
  `sudo -u tgw tgw dead-letter --cancel <queue>` for new dead letters, or for queued
  clones:

  ```bash
  psql -U tgw state_machine -c "
    UPDATE queue_jobs SET state='cancelled'
    WHERE job_id='<NEW_JOB_ID>' AND state='queued';"
  ```

  (Manual SQL is a last resort — the state machine normally owns all transitions.)
- Cancelled something you still need: enqueue a fresh job —
  `sudo -u tgw tgw enqueue-sku <queue> <sku>`.
- Side effects of a half-run job are absorbed by idempotent handlers; no item-level
  rollback is needed.

## Verification

```bash
# 1. Dead-letter counts back to zero (or only known/parked items)
sudo -u tgw tgw health
sudo -u tgw tgw dead-letter

# 2. Requeued jobs actually progressed
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs
  GROUP BY 1,2 ORDER BY 1,2;"
# expect requeued jobs to move queued → succeeded, not back to dead_letter

# 3. Spot-check an affected item end state
sudo -u tgw tgw get <SKU>     # confirm the expected block appeared
                              # (draft_listing / ebay_photos / price / offer_id)

# 4. No fresh error notifications
tail -20 /opt/TGW/var/log/notifications.jsonl
```
