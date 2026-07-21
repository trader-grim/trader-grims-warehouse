# In progress: todo #1608 — PP-STATEMACHINE-001 job manifest (4 phases)

Working in worktree `/opt/TGW/var/worktrees/1608-statemachine-manifest` on branch
`todo/1608-statemachine-manifest`. Implementing:
- Phase 1: dedupe_key fixes for 8 self-rescheduling workers + ebay_upload quota-retry +
  ebay_sync per-sku manual triggers, per #1607 audit.
- Phase 2: tgw-queue-priorities.json + enqueue_job() config lookup.
- Phase 3: supersede flag + atomic cancel-then-insert, wire restart-ebay-token CLI.
- Phase 4 (only after 1-3 tested clean): flip enqueue_job() enforcement on, write invariant E16.

Result manifest goes to `docs/TGW-Plan-Vault/plan/packets/results/1608-RESULT.md`.
