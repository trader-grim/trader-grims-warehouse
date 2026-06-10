# Runbook: Ollama inference stall

**Failure mode:** local Ollama (CPU-only, 32 GB host) is down, overloaded, or the global
serialization lock is stuck, so every inference consumer stalls: `ai_identify` (vision,
`qwen2.5vl:7b`), `ebay_draft` (aspects, `qwen2.5:latest`), and `pm_intake` (plan patching).
The intake pipeline backs up at identification; nothing downstream gets new work.

Design context: **all inference is serialized** through a session-level Postgres advisory
lock, id **8472** (`src/tgw/queue/ollama_lock.py`) — two concurrently loaded models thrash
the machine. Workers that can't get the lock **block silently** (`pg_advisory_lock`),
they do not fail or skip. CPU inference is slow by nature — minutes per item is normal,
not an incident (see `reference/HARDWARE-AI-INFERENCE.md`).

## Symptoms

- `ai_identify` / `ebay_draft` queues grow; jobs sit in `leased`/`running` far longer
  than usual, possibly until lease expiry (default 300 s) and requeue.
- `journalctl -u tgw-worker@ai_identify.service` shows long gaps, or
  `ollama_lock: acquired after N s` with large N.
- `curl http://localhost:11434/api/tags` fails or hangs (Ollama down).
- Host load/memory pegged; OOM-killer events in `dmesg`.
- Repeated lease-expiry → requeue cycles for the same SKU (job never finishes inside the
  lease while inference grinds).

## Likely root causes

1. **ollama.service down or wedged** (crash, OOM, failed model load).
2. **Stuck advisory lock**: a connection holding lock 8472 never released it — e.g. a
   worker process killed -9 while its dedicated lock connection survived in a strange
   state, or a manual script that took the lock and hung. (Session-level locks release
   when the *connection* dies, so a truly orphaned lock means the holding backend still
   exists in `pg_stat_activity`.)
3. **Model not pulled / disk full** — model load fails on every request.
4. **Memory pressure**: something else big is running; the 7B vision model + system
   doesn't fit, swap-thrash makes inference "stuck" rather than slow.
5. **Lease too short for the job**: very slow inference exceeding the 300 s lease causes
   requeue-while-still-running churn (the original run's write is then a harmless
   idempotent duplicate, but throughput collapses).
6. **Postgres down** → lock acquisition itself fails (runbook 4 first).

## Diagnosis

```bash
# 1. Is Ollama alive and responsive?
systemctl status ollama.service
curl -s http://localhost:11434/api/tags | head -c 400        # model list
curl -s http://localhost:11434/api/ps                         # what's loaded now

# 2. Who holds / waits on the inference lock?
psql -U tgw state_machine -c "
  SELECT l.pid, a.usename, a.application_name, a.state,
         a.query_start, l.granted
  FROM pg_locks l JOIN pg_stat_activity a USING (pid)
  WHERE l.locktype='advisory' AND l.objid=8472;"
# one granted row = normal (someone is inferring); granted row with an ancient
# query_start and a dead-looking client = stuck holder

# 3. Worker-side view
journalctl -u tgw-worker@ai_identify.service --since "-1 hour"
journalctl -u tgw-worker@ebay_draft.service --since "-1 hour"
# look for 'ollama_lock: acquired after X s' lines and HTTP errors to :11434

# 4. Queue churn (lease-expiry cycling)
psql -U tgw state_machine -c "
  SELECT job_id, payload_json->>'sku' AS sku, state, attempt_count,
         lease_expires_at, updated_at
  FROM queue_jobs WHERE queue_name='ai_identify'
    AND state IN ('queued','leased','running','retry_wait')
  ORDER BY updated_at DESC LIMIT 15;"

# 5. Host resources
free -h; uptime
dmesg -T | grep -i -E 'oom|killed process' | tail
df -h /        # model storage (~/.ollama or /usr/share/ollama)
```

## Recovery

```bash
# Ollama down/wedged:
sudo systemctl restart ollama.service
curl -s http://localhost:11434/api/tags    # confirm it answers before moving on

# Model missing:
sudo -u tgw ollama pull qwen2.5vl:7b
sudo -u tgw ollama pull qwen2.5:latest

# Stuck lock holder (confirmed ancient + dead client in step 2):
psql -U tgw state_machine -c "SELECT pg_terminate_backend(<PID>);"
# terminating the backend releases the session-level lock; waiting workers proceed
# immediately. Do NOT terminate a holder that is actively inferring (recent
# query_start, worker journal shows progress) — that's just CPU-slow.

# Memory pressure: stop the competing load. Do not try to run two models —
# the single-flight lock exists because the host can't.

# Worker processes that blocked for hours: restart to clear any odd state
sudo systemctl restart tgw-worker@ai_identify.service tgw-worker@ebay_draft.service

# Backlog: nothing special needed — jobs are queued and idempotent; they drain
# serially once inference works. Re-drive anything that dead-lettered:
sudo -u tgw tgw dead-letter --requeue-transient
```

## Rollback

- Restarting ollama.service / workers / terminating a stuck lock backend needs no
  rollback — the lock is reacquired per call, jobs requeue, handlers are idempotent.
- If you terminated an *active* inference by mistake: the job fails or its lease expires,
  it requeues, and the item is re-identified on the next pass. `identification_history`
  in the item JSON records every run, so a half-written identification is visible and
  simply superseded.
- If you pulled/changed model versions and identification quality dropped: re-pull the
  pinned tags above (the prompts in `reference/TGW-Ollama-Prompts.md` are tuned for
  qwen2.5vl:7b / qwen2.5) and re-identify affected items:
  `sudo -u tgw tgw hint <SKU> "<correction>"` or
  `sudo -u tgw tgw requeue --unidentified --run`.

## Verification

```bash
# 1. Inference round-trip directly
curl -s http://localhost:11434/api/generate -d '{
  "model": "qwen2.5:latest", "prompt": "say ok", "stream": false}' | head -c 300

# 2. Lock flowing (rows should change over a few minutes, not freeze on one pid)
psql -U tgw state_machine -c "
  SELECT l.pid, a.query_start, l.granted FROM pg_locks l
  JOIN pg_stat_activity a USING (pid)
  WHERE l.locktype='advisory' AND l.objid=8472;"

# 3. Queues draining
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs
  WHERE queue_name IN ('ai_identify','ebay_draft') GROUP BY 1,2 ORDER BY 1,2;"

# 4. An item actually progressed
sudo -u tgw tgw get <SKU>     # ai_identified: true, fresh identification_history entry

# 5. Throughput sanity over an hour (CPU-slow is normal; zero is not)
psql -U tgw state_machine -c "
  SELECT count(*) FROM queue_job_history h JOIN queue_jobs j USING (job_id)
  WHERE j.queue_name='ai_identify' AND h.new_state='succeeded'
    AND h.created_at > now() - interval '1 hour';"
```
