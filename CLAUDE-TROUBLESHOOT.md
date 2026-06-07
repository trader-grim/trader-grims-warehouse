# TGW Troubleshooting Guide (for `tgw claude-help`)

Condensed operational reference for diagnosing a live TGW incident. Loaded as the
system prompt for `tgw claude-help` (PP-CLAUDE-HELP-001). Keep in sync with
`docs/TGW-Plan-Vault/reference/ISSUES.md`.

## System shape (worker → queue → DB)

- **tgw-api is the fence** — all ItemData reads/writes go through it. Item JSON at
  `/opt/TGW/data/ItemData/<SKU>/<SKU>.json`.
- **PostgreSQL `state_machine` is the work ledger.** Table `queue_jobs`; states:
  `queued → leased → running → succeeded | retry_wait | dead_letter | cancelled`.
- **Workers are thin systemd units** `tgw-worker@<queue>.service`, each claims jobs
  from its queue via the shared `QueueWorker` base (claim/lease/complete/fail).
- **Pipeline order:** photo intake → `ai_identify` (+ barcode/product lookup) →
  `ebay_draft` → `ebay_upload` → `ebay_price` → `ebay_stage` → operator
  `tgw staged`/`tgw publish` → `ebay_sync`.
- **Catalog rebuild is always a job** (`catalog_rebuild` queue), coalesced.

## First moves

```bash
tgw health                         # config, Postgres, SQLite, thumbnails, token, Ollama
systemctl list-units 'tgw-worker@*'
journalctl -u 'tgw-worker@<queue>.service' -n 100 --no-pager
psql -U tgw state_machine -c "SELECT queue_name, state, count(*) FROM queue_jobs GROUP BY 1,2 ORDER BY 1,2;"
tgw dead-letter                    # dead_letter jobs + transient/permanent verdict
```

## Decision tree

- **A worker isn't processing** → is the unit active (`systemctl status`)? Is Postgres
  up (`tgw health`)? Are jobs stuck in `leased`/`running` past their lease?
  `recover_expired_jobs()` promotes expired leases back to `queued`.
- **Jobs piling in `dead_letter`** → `tgw dead-letter`; `[transient]` verdict →
  re-enqueue with `tgw dead-letter --requeue <JOB_ID>` (dead_letter never auto-retries).
  `[permanent]` → read `error_detail`, fix root cause, then requeue.
- **eBay calls failing 4xx/auth** → the OAuth token is likely dead (see ISS-009).
  Token is operator-restored; nothing code-side fixes a dead refresh token.
- **Publish fails errorId 25002 (Item.Country)** → ISS-001; offer needs
  `availabilityDistributions` + `merchantLocationKey` (already in `sync.py`).
- **Condition rejected (25021)** → category only accepts coarse conditions; the
  draft/publish path retries with `USED_EXCELLENT` (→ conditionId 3000).
- **A worker change isn't taking effect** → `systemctl restart tgw-worker@<queue>.service`.

## Known issues (see ISSUES.md for detail)

- **ISS-009** — eBay refresh token DEAD (HTTP 400 invalid_grant). Operator must
  re-consent (`get_access_token.py`) then `tgw restart-ebay-token`. Blocks all live
  eBay GET/PUT/POST. **Most "eBay broke" incidents trace here first.**
- **ISS-001** — errorId 25002 at publish; code fix applied, re-publish to confirm.
- **ISS-002 / ISS-005 / ISS-008** — operator/token-gated, not code.

## Guardrails

- Commit only when Dave asks. Run all commands as the `tgw` user. Run `tgw health`
  after touching config/secrets/workers. Never add eBay scopes speculatively
  (broke OAuth once). Re-enqueue manually after `dead_letter`.
