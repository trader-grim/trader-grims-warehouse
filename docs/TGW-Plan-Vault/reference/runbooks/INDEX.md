# TGW Operational Runbooks

**Status:** living documents. Created 2026-06-10 from `docs/architecture/{overview,services}.md`,
`docs/invariants.md`, `reference/ISSUES.md`, `reference/eBay-Error-Codes.md`, and the worker
source. Each runbook covers: symptoms → likely root causes → diagnosis → commands → rollback →
verification.

**Ground rules for every incident:**

- Run everything as the `tgw` user (`sudo -u tgw ...`). Source files are `rw-------`,
  secrets are `chmod 600`.
- Start with `sudo -u tgw tgw health` — it checks config paths, Postgres (with per-queue
  dead_letter breakdown), SQLite catalog, thumbnails, and permissions.
- Workers pick up source changes only after `systemctl restart tgw-worker@<queue>.service`
  (or `sudo -u tgw tgw restart-workers [queues...]`). A deploy is not done until the
  affected units restart.
- Dead-letter jobs **never auto-retry** — they need a human (see the dead-letter runbook).
- Commit only when Dave asks. Never change eBay OAuth scopes — a speculative scope change
  killed the refresh token on 2026-06-05.

## Runbooks, by production impact

| # | Runbook | Failure mode | Blast radius |
|---|---------|--------------|--------------|
| 1 | [ebay-token-failure.md](ebay-token-failure.md) | eBay OAuth token expired / refresh token dead | **Every eBay-touching worker degrades** (upload, price, stage, publish, sync, reducer, migrate) |
| 2 | [dead-letter-triage.md](dead-letter-triage.md) | Jobs landing in dead_letter; silent pileups | Per-item pipeline stops; nothing recovers without an operator |
| 3 | [pipeline-stall.md](pipeline-stall.md) | Item stuck between intake and staged; worker down or zero-work looping | New inventory stops reaching eBay |
| 4 | [postgres-outage.md](postgres-outage.md) | PostgreSQL down or work ledger unhealthy | All 18 workers stop; CLI queue/todo commands fail (item reads still work) |
| 5 | [ebay-stage-publish-rejections.md](ebay-stage-publish-rejections.md) | eBay API rejects staging/publish (25021, 25002, etc.); duplicate-listing risk | Items can't go live; worst case duplicate or stripped listings (money) |
| 6 | [catalog-stale.md](catalog-stale.md) | SQLite catalog / search / location tree / thumbnails stale or broken | All list/search surfaces (CLI, Flutter, web forms) show wrong data |
| 7 | [ollama-inference-stall.md](ollama-inference-stall.md) | Ollama down/slow or advisory lock 8472 stuck | ai_identify, ebay_draft, pm_intake stall — intake pipeline backs up |
| 8 | [sold-sync-gaps.md](sold-sync-gaps.md) | Sold on eBay but still "available" locally; sold-event loss | Oversell / re-list of sold items; bad velocity data |

## Quick triage — "something is wrong, where do I start?"

```bash
# 1. Overall health (includes per-queue dead_letter counts)
sudo -u tgw tgw health

# 2. Queue state at a glance
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs
  GROUP BY queue_name, state ORDER BY queue_name, state;"

# 3. Are the worker processes alive?
systemctl list-units 'tgw-worker@*'

# 4. What are the dead letters?
sudo -u tgw tgw dead-letter

# 5. Recent worker errors
journalctl -u 'tgw-worker@*' --since "-2 hours" -p warning
```

Decision guide from the output:

- `dead_letter` rows for `token_refresh`, or errors containing `token is expired` /
  `invalid_grant` → **runbook 1**.
- `dead_letter` rows for anything else → **runbook 2** (it routes to 5 for eBay errorIds).
- Jobs sitting in `queued`/`retry_wait` with no progress, or a unit inactive → **runbook 3**.
- `psql` itself fails / workers flapping on restart → **runbook 4**.
- Search/list results don't match `tgw get <sku>` (the JSON is truth) → **runbook 6**.
- `ai_identify`/`ebay_draft` queued forever, `journalctl` shows lock waits → **runbook 7**.
- An item sold on eBay still shows available locally → **runbook 8**.

## Architecture facts that shape every recovery

- **ItemData JSON is canonical for item state; Postgres `state_machine` is canonical for
  work state.** Everything else (SQLite catalog, thumbnails, location tree, velocity stats)
  is derived and regenerable — deleting/rebuilding derived stores is always safe.
- **All jobs are idempotent** — each pipeline worker has a skip condition (already
  identified, draft present, photos uploaded, price set, offer_id present, listing Active).
  Re-running a job is the default safe recovery move.
- **Re-enqueue after dead_letter needs a fresh dedupe key** — `tgw dead-letter --requeue`
  handles this (clones the job without a dedupe key).
- **Transient classification is substring matching** (`_TRANSIENT_ERRORS` in
  `src/tgw/queue/worker_base.py`): `token is expired`, `no ebay photo urls yet`,
  `directory not empty`, `readtimeout`, `lease_expired`, `connectionerror`. A new error
  phrasing dead-letters until the list is extended.
- **Publish is operator-gated.** No recovery procedure may push an item to `status=Active`
  except `tgw publish <sku>` after `tgw staged` review.
