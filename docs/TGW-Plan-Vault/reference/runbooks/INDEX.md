# TGW Operational Runbooks

**Status:** living documents. Created 2026-06-10 from `docs/architecture/{overview,services}.md`,
`docs/invariants.md`, `reference/ISSUES.md`, `reference/eBay-Error-Codes.md`, and the worker
source. Each runbook covers: symptoms → likely root causes → diagnosis → commands → rollback →
verification.

**Ground rules for every incident:**

- Run application/data operations as the `tgw` user (`sudo -u tgw ...`). Immutable
  release selection is the documented exception: use the privileged release-operator
  boundary with bytecode writes disabled, as specified in the PP-WORKFLOW-001 rollout
  runbook. Source files are `rw-------`, secrets are `chmod 600`.
- Start with `sudo -u tgw tgw health` — it checks config paths, Postgres (with per-queue
  dead_letter breakdown), SQLite catalog, thumbnails, and permissions.
- Workers pick up source changes only after `systemctl restart tgw-worker@<queue>.service`
  (or `sudo -u tgw tgw restart-workers [queues...]`). A deploy is not done until the
  affected units restart.
- Dead-letter jobs **never auto-retry** — they need a human (see the dead-letter runbook).
- Commit only when Dave asks. Never change eBay OAuth scopes — a speculative scope change
  killed the refresh token on 2026-06-05.
- **Verify state directly before declaring anything missing (report gap #13,
  PP-RECOVERY-001, todo #1529/PP-RUNBOOK-001).** PP-RECOVERY-001 (2026-06-17)
  is the standing cautionary example: a session concluded real code/work had
  been lost from an incomplete `"todo #NNN"` commit-message grep and a branch
  comparison against `main` alone — the work was actually present, just on an
  unmerged branch and behind an editable install. It took a direct state
  check (branch diff, running service's actual source, live route walk) to
  disprove the false alarm. Before declaring code, data, or a completed task
  missing: check the actual running/editable-install source, check
  non-`main` branches, and check live behavior — not just one grep or one
  branch's file list. See `plan/pp/PP-RECOVERY-001.md` for the full incident.

## Runbook metadata convention (report gap #7, todo #1529/PP-RUNBOOK-001)

Every runbook in this directory (and the restore/vault/DR docs in
`reference/`) should carry these four facts near the top, so an operational
agent can judge freshness/applicability before acting on it rather than only
discovering staleness mid-incident:

- **Owner** — who maintains it (Dave, Claude, Tigwa, or "shared").
- **Last verified** — date it was last checked against live system state
  (not just last edited).
- **Applies to** — host/OS generation this procedure is valid for (e.g.
  "tgw-prod, NixOS, post-2026-06-23 cutover" vs. "historical, pre-NixOS
  MX era").
- **Last drill** — date of the last successful end-to-end test of the
  procedure it describes, or "never drilled" if that's still true (don't
  omit this rather than admit it — see USB restore gap in
  `TGW-VAULT-RESTORE.md`).

**Status of this convention itself (2026-07-18):** defined here, applied to
`TGW-VAULT-RESTORE.md`, `PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md`, and
`nixos-prod-cutover-runbook.md` as part of this same triage pass (applicability
banners). Not yet retroactively applied to the 10 numbered runbooks below —
filed as todo #1533 (`--pp PP-RUNBOOK-001`), a mechanical per-file task
suited to the Aider/DeepSeek busywork tier rather than a fix bundled into
this packet.

## Runbooks, by production impact

### PP-WORKFLOW-001 suite (2026-08-10)

These runbooks govern graph-bound workflow work and supersede the older blanket
“all jobs are safe to requeue” guidance for governed or provider-effect jobs:

- [Operator overview and triage](pp-workflow-001-operations.md)
- [Deployment, selector rollout, and rollback](pp-workflow-001-rollout.md)
- [Item, attempt, timer, and projection recovery](pp-workflow-001-item-recovery.md)
- [Provider-effect ambiguity and reconciliation](pp-workflow-001-provider-reconciliation.md)
- [Production acceptance checklist](pp-workflow-001-acceptance.md)

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
| 9 | [thermal-emergency-response.md](thermal-emergency-response.md) | tgw-prod thermal HOT/THROTTLE/SHUTDOWN | Host may shut down; formal policy for Tigwa-lite's monitor response (not an operator-diagnosis runbook) |
| 10 | [ebay-api-operations.md](ebay-api-operations.md) | eBay API quota/rate-limit exhaustion; 25707 orphaned-offer bulk-fetch cascade (todo #1077); Inventory API empty-aspect-value rejection (invariant C14) | Pipeline-wide eBay API drain; permanently-degraded bulk sync; operator corrections silently lost / listing manually ended |

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
- `429`/"api token limit exhausted" from any eBay API family, `ebay_sync`
  logging `eBay error 25707` or a multi-thousand "checked N SKUs" fallback
  pass, or an operator's cleared field/aspect silently not persisting on a
  push → **runbook 10**.

## Architecture facts that shape every recovery

- **ItemData JSON is canonical for item state; Postgres `state_machine` is canonical for
  work state.** Everything else (SQLite catalog, thumbnails, location tree, velocity stats)
  is derived and regenerable — deleting/rebuilding derived stores is always safe.
- **Legacy jobs are generally idempotent; governed jobs are generation-bound.** Never
  blindly replay a job carrying graph/generation/condition identity. Provider-effect
  ambiguity always requires reconciliation. Use the PP-WORKFLOW-001 suite above.
- **Legacy re-enqueue after dead_letter needs a fresh dedupe key** —
  `tgw dead-letter --requeue` handles this. Do not use it for graph-bound or ambiguous
  provider work; preserve the attempt and re-evaluate instead.
- **Transient classification is substring matching** (`_TRANSIENT_ERRORS` in
  `src/tgw/queue/worker_base.py`): `token is expired`, `no ebay photo urls yet`,
  `directory not empty`, `readtimeout`, `lease_expired`, `connectionerror`. A new error
  phrasing dead-letters until the list is extended.
- **Publish is operator-gated.** No recovery procedure may push an item to `status=Active`
  except `tgw publish <sku>` after `tgw staged` review.
