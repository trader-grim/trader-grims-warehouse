# Runbook: PostgreSQL outage / work-ledger failure

**Failure mode:** the `state_machine` database (the work ledger) is down, unreachable, or
unhealthy. Every worker has `Requires=postgresql.service`, so all 18 stop or flap. The
Ollama advisory lock (8472) also lives in Postgres, so inference serialization fails too.

**What keeps working:** item reads/writes through the fence (`tgw get`, `tgw set`, HTTP
GET/PATCH) — ItemData is filesystem JSON and does not need Postgres. What breaks: every
queue/todo/health subcommand, all workers, all pipeline progress, and the catalog-rebuild
*signal* (writes still land in JSON; the rebuild enqueue fails — see the staleness note
below).

## Symptoms

- `psql -U tgw state_machine -c 'SELECT 1;'` fails (connection refused / could not
  connect).
- `systemctl list-units 'tgw-worker@*'` shows units failed/restarting in a loop.
- `tgw health` fails its Postgres section; queue subcommands error out.
- Disk-full variant: Postgres logs `No space left on device`; may degrade before a full
  outage.

## Likely root causes

1. **postgresql.service stopped/crashed** (OOM, manual stop, failed upgrade).
2. **Disk full** on the Postgres data volume (also breaks WAL).
3. **Connection exhaustion** — each worker holds connections, plus a dedicated one per
   Ollama lock acquisition; runaway agents/scripts can exhaust `max_connections`.
4. **Boot-order race** — workers start 10 s after boot via `queue-workers-startup.timer`;
   if Postgres is slow to come up, units may fail their first start (systemd restarts
   them; persistent failure means Postgres still isn't up).
5. **Schema/migration damage** — `queue_jobs`, `claim_queue_jobs()` or
   `recover_expired_jobs()` missing/broken after a botched change.

## Diagnosis

```bash
# 1. Service and basic connectivity
systemctl status postgresql.service
psql -U tgw state_machine -c 'SELECT 1;'

# 2. Postgres' own logs
journalctl -u postgresql.service --since "-2 hours"

# 3. Disk space (data volume + WAL)
df -h /var/lib/postgresql /opt/TGW

# 4. Connection pressure
psql -U tgw state_machine -c "
  SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
psql -U tgw state_machine -c "SHOW max_connections;"

# 5. Ledger integrity (after connectivity is back)
psql -U tgw state_machine -c "\df claim_queue_jobs"
psql -U tgw state_machine -c "\df recover_expired_jobs"
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs GROUP BY 1,2 ORDER BY 1,2;"

# 6. Worker units' view of the world
systemctl list-units 'tgw-worker@*' --all
journalctl -u 'tgw-worker@*' --since "-30 min" | tail -50
```

## Recovery

```bash
# 1. Service down → start it
sudo systemctl start postgresql.service
journalctl -u postgresql.service -f      # watch for clean recovery / WAL replay

# 2. Disk full → free space FIRST (never delete WAL files by hand).
#    Likely reclaim targets: /opt/TGW/var/log/*, old sku-migrate manifests,
#    journald (journalctl --vacuum-size=500M). Then start Postgres.

# 3. Connection exhaustion → find and stop the hog, then:
psql -U tgw state_machine -c "
  SELECT pg_terminate_backend(pid) FROM pg_stat_activity
  WHERE state='idle' AND state_change < now() - interval '1 hour'
    AND pid <> pg_backend_pid();"

# 4. Once Postgres answers, restart the worker fleet (don't trust flapped units):
sudo systemctl restart 'tgw-worker@*'
# or: sudo -u tgw tgw restart-workers

# 5. Recover leases that expired during the outage (also runs automatically
#    in every worker every 60 s):
psql -U tgw state_machine -c "SELECT recover_expired_jobs();"

# 6. Schema damage → restore schema objects from the repo:
#    src/tgw/queue/schema.sql  (review before applying; it is the canonical DDL)
```

**Catalog staleness after the outage:** any ItemData write made while Postgres was down
could not enqueue its `catalog_rebuild`. After recovery, force one:

```bash
sudo -u tgw tgw build-all
```

(Inline rebuild is acceptable from the operator CLI — the never-inline rule is for
worker code.)

## Rollback

- Starting/restarting Postgres and workers needs no rollback.
- If you applied `schema.sql` over a live schema and something regressed: the
  `queue_job_history` table is append-only audit — job rows can be reconstructed in the
  worst case, but the practical rollback is restoring the database from the most recent
  dump. **Known gap:** file snapshots (trader-grims-backup) do NOT give a consistent
  Postgres copy (live WAL); only `pg_dump` output counts. If no recent dump exists, accept
  ledger loss: the ledger is work state, not item state — ItemData JSON is intact, and the
  pipeline can be re-driven (`tgw requeue --unidentified/--no-draft/--no-price --run`)
  to rebuild queue state from item reality.
- Terminated backends reconnect on their own (workers loop; clients retry).

## Verification

```bash
# 1. Postgres healthy
psql -U tgw state_machine -c 'SELECT now();'
systemctl status postgresql.service

# 2. All worker units active (not flapping — check twice a minute apart)
systemctl list-units 'tgw-worker@*'

# 3. Ledger functioning end-to-end: echo probe
sudo -u tgw tgw enqueue-sku echo <any-sku>
psql -U tgw state_machine -c "
  SELECT state FROM queue_jobs WHERE queue_name='echo'
  ORDER BY created_at DESC LIMIT 1;"        # expect 'succeeded' within ~1 min

# 4. No stuck leases
psql -U tgw state_machine -c "
  SELECT count(*) FROM queue_jobs
  WHERE state IN ('leased','running') AND lease_expires_at < now();"  # expect 0

# 5. Full health (includes per-queue dead_letter breakdown + catalog check)
sudo -u tgw tgw health

# 6. Catalog caught up (see catalog-stale.md if not)
sudo -u tgw tgw list --search "<recently edited term>"
```
