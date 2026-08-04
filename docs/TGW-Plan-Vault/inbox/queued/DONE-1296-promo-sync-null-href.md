Worked todo #1296 (PP-COHESION-001) on branch todo/1296-promo-sync-null-href in
worktree /opt/TGW/var/worktrees/1296-promo-sync-null-href. Fixed
cmd_promo_sync() in src/tgw/promo.py: `promo_summary.get("promotionHref", "")`
crashed with AttributeError when the key was present but explicitly None
(dict.get default only applies when key is absent). Applied the packet's exact
fix: `(promo_summary.get("promotionHref") or "").split("/")[-1]`. Added 3
regression tests (TestPromoSyncNullHref in tests/test_promo.py) covering the
packet's 3 acceptance cases. Full offline suite green (2049 passed, 1 skipped),
verified against the worktree's own copy via PYTHONPATH override. Result
manifest written to docs/TGW-Plan-Vault/plan/packets/results/1296-RESULT.md.
Status: done, committed on branch. Nothing further pending for this task.
