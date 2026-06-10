# Runbook: pipeline stall

**Failure mode:** items stop moving through the intake → identify → draft → upload/price
→ stage flow. Distinct from dead-letter (runbook 2): here jobs are *not* failing visibly —
they're queued forever, looping in retry_wait, or a worker is down or silently doing zero
work.

The reference flow (who enqueues whom) is in
`docs/TGW-Plan-Vault/reference/TGW-Pipeline-Flow.md`. Expected stage order in the item
JSON: stub → `ai_identified: true` → `draft_listing` → `ebay_photos` +
`draft_listing.price` → `ebay_offer.offer_id` (UNPUBLISHED) → operator `tgw publish`.

## Symptoms

- Photo drops in `incoming/newitems/<SKU>/` not turning into ItemData folders.
- Items with `ai_identified: true` but no `draft_listing` (or similar gap at any stage).
- `tgw staged` hasn't gained items in hours despite intake activity.
- Queue counts growing in `queued`/`retry_wait` for one queue while others drain.
- A worker unit alive but its queue not shrinking (**zero-work stall** — the 2026-06-08
  `ebay_sku_migrate` incident: batch capacity exhausted on 5 permanently-failing items
  with zero visible errors).

## Likely root causes

1. **Worker process down or never restarted after a deploy.** Source changes require
   `systemctl restart tgw-worker@<queue>.service` — a stale worker runs old code.
2. **Unbounded transient loop** (invariant D7): `requeue_with_backoff` resets
   `attempt_count`, so a "transient" error that never resolves (token never refreshed,
   ebay_upload never completing) loops forever with only warning notifies.
   Common loop markers: `token is expired` (900 s), `no ebay photo urls yet` (600 s).
3. **Zero-work stall**: worker claims its batch, every item fails the same way, batch
   capacity exhausts, repeat. Queue looks active; nothing completes.
4. **Upstream gate not met**: `ebay_price` left price null (thin comps — item deliberately
   stalls, see below); `ebay_upload` partial (stage requires photos); `ebay_stage` guard
   tripping (Active listing / legacy `Item number`).
5. **Ollama stalled** → `ai_identify`/`ebay_draft` blocked (runbook 7).
6. **Intake stability gate**: `bundle_intake` waits for a drop directory to be unmodified
   for 30 s — a stalled/resuming transfer (> 30 s pause then resume) can yield a
   half-consumed drop.
7. **Postgres degraded** → everything (runbook 4).

## Diagnosis

```bash
# 1. Where is the backlog?
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*), min(created_at) AS oldest
  FROM queue_jobs WHERE state IN ('queued','leased','running','retry_wait')
  GROUP BY 1,2 ORDER BY 1,2;"

# 2. Are the workers alive (and recently restarted if code changed)?
systemctl list-units 'tgw-worker@*'
systemctl status tgw-worker@<queue>.service

# 3. What is the stuck worker actually doing?
journalctl -u tgw-worker@<queue>.service --since "-1 hour"

# 4. Transient loops: same jobs cycling through retry_wait
psql -U tgw state_machine -c "
  SELECT job_id, payload_json->>'sku' AS sku, attempt_count, error_detail, not_before
  FROM queue_jobs WHERE queue_name='<queue>' AND state='retry_wait'
  ORDER BY not_before LIMIT 20;"

# 5. Zero-work stall: completions over time — flat line = stalled
psql -U tgw state_machine -c "
  SELECT date_trunc('hour', h.created_at) AS hour, count(*)
  FROM queue_job_history h JOIN queue_jobs j USING (job_id)
  WHERE h.new_state='succeeded' AND j.queue_name='<queue>'
    AND h.created_at > now() - interval '24 hours'
  GROUP BY 1 ORDER BY 1;"

# 6. For one stuck SKU: which stage gate is unmet?
sudo -u tgw tgw get <SKU>
# check in order: ai_identified → draft_listing → ebay_photos / draft_listing.price
#                 → ebay_offer.offer_id

# 7. Stuck intake: drops not consumed
ls -la /opt/TGW/data/incoming/newitems/
journalctl -u tgw-worker@bundle_intake.service --since "-2 hours"
```

**Deliberate stalls that are not incidents:**

- **Unpriced items**: with < 3 comps and no group/config fallback, price stays null and
  `ebay_stage` is *not* enqueued (invariant B5 — never guess a price). Fix the item, not
  the queue: set a price manually or fix the category-group mapping, then
  `sudo -u tgw tgw requeue --no-price --run`.
- **Staged items waiting**: everything after `ebay_stage` is the operator gate.
  `tgw staged` review + `tgw publish <sku>` is the only way forward — by design.

## Recovery

```bash
# Worker down / stale code:
sudo systemctl restart tgw-worker@<queue>.service
# or several at once:
sudo -u tgw tgw restart-workers <queue1> <queue2>

# Transient loop: fix the dependency (token → runbook 1; upload → requeue ebay_upload),
# the loop then resolves itself on the next backoff expiry. To force immediately:
psql -U tgw state_machine -c "
  UPDATE queue_jobs SET not_before = now()
  WHERE queue_name='<queue>' AND state='retry_wait';"

# Zero-work stall: identify the poison items from the journal, park them
# (e.g. set a skip flag / fix the data), then restart the worker so the batch refills.

# Stage-gap on specific items: re-drive the missing stage
sudo -u tgw tgw enqueue-sku <queue> <sku>           # one item
sudo -u tgw tgw requeue --unidentified --run         # bulk: ai_identify
sudo -u tgw tgw requeue --no-draft --run             # bulk: ebay_draft
sudo -u tgw tgw requeue --no-price --run             # bulk: ebay_price

# Half-consumed intake drop: verify media completeness in ItemData/<SKU>/;
# copy missing files into the SKU folder (as tgw), then re-enqueue thumbnail/identify.
# Intake never overwrites an existing <SKU>.json, so re-dropping the same SKU dir is safe
# only for media — the stub JSON wins.

# Expired leases (worker crashed mid-job): recovered automatically every 60s by all
# workers via recover_expired_jobs(); to nudge manually:
psql -U tgw state_machine -c "SELECT recover_expired_jobs();"
```

## Rollback

- Restarting workers is always safe — jobs are lease-based; an interrupted job's lease
  expires and `recover_expired_jobs()` requeues it; handlers are idempotent.
- Bulk `tgw requeue --run` issued too broadly: the skip conditions make redundant jobs
  no-ops; to stop a large wave before it processes,
  `sudo -u tgw tgw dead-letter --cancel <queue>` won't help (they're queued, not
  dead-lettered) — cancel directly:

  ```bash
  psql -U tgw state_machine -c "
    UPDATE queue_jobs SET state='cancelled'
    WHERE queue_name='<queue>' AND state='queued'
      AND created_at > '<wave start timestamp>';"
  ```

- Forcing `not_before = now()` has no rollback need — worst case the jobs fail again and
  re-enter backoff.

## Verification

```bash
# 1. Backlog draining
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs
  GROUP BY 1,2 ORDER BY 1,2;"        # run twice, 10 min apart — counts must move

# 2. Completions resumed
psql -U tgw state_machine -c "
  SELECT j.queue_name, count(*)
  FROM queue_job_history h JOIN queue_jobs j USING (job_id)
  WHERE h.new_state='succeeded' AND h.created_at > now() - interval '30 minutes'
  GROUP BY 1;"

# 3. End-to-end probe through the queue machinery
sudo -u tgw tgw enqueue-sku echo <any-sku>   # echo worker = liveness probe
journalctl -u tgw-worker@echo.service -n 20

# 4. The originally-stuck item reached its next stage
sudo -u tgw tgw get <SKU>

# 5. Health clean
sudo -u tgw tgw health
```
