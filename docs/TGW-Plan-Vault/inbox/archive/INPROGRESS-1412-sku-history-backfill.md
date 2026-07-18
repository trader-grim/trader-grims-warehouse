# In progress: todo #1412 (PP-ADD-005) — sku_history backfill investigation

Working in worktree `/opt/TGW/var/worktrees/1412-sku-history-backfill-investigation`
on branch `todo/1412-sku-history-backfill-investigation`.

Task: sku_history table has only 3,305 rows but PP-ADD-005 docstring implies ~34k+
renames executed (Class A bulk ~26,423 + Class A live-eBay ~8,314). Investigating
whether rename_sku() was bypassed for bulk runs, and whether
/opt/TGW/var/log/sku-migrate-*.json manifests can safely backfill missing rows
(no fabrication — Prime Directive 1).

Status: DONE (partial — investigation complete, backfill script coded + dry-run
verified live, real INSERT blocked pending Dave's decision). See
docs/TGW-Plan-Vault/plan/packets/results/1412-sku-history-backfill-RESULT.md
for full findings. Root cause: rename_sku() was never bypassed; a 2026-06-24
pg_restore during the NixOS/CatioNIX cutover (commit 234ff84) dropped the
June 3-4 bulk-migration sku_history rows. 26,652 rows are safely recoverable
from /opt/TGW/var/log/sku-migrate-*.json manifests, all disk-verified. Todo
#1509 filed for Dave to review/apply.
