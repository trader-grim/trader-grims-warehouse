# In progress: todo #1406 (PP-DEADLETTER-001) — entity_id passthrough fix

Working in isolated worktree `/opt/TGW/var/worktrees/1406-entity-id-passthrough` on branch
`todo/1406-entity-id-passthrough`. Fixing `queue_jobs.entity_id` falling back to `queue_name`
for internal pipeline cross-enqueue calls (ebay_draft->ebay_upload, ebay_price->ebay_stage,
ebay_stage->ebay_publish, etc.) that never pass `entity_id=sku`, breaking
`tgw queue-history --sku <sku>`. Scope: code fix only, pass `entity_id=sku` at every internal
`enqueue_job(...)` call site in `src/tgw/workers/*.py` where SKU is in scope; add a
regression-preventing comment/test; NO backfill/migration of the 302,841 existing bad rows
(read-only sampling only, findings noted in result manifest). Verifying live via a fresh item
pushed through the real pipeline post-fix.
