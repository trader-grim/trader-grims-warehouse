Working on todo #1297 (PP-COHESION-001) in worktree
/opt/TGW/var/worktrees/1297-ebay-sku-migrate-default on branch
todo/1297-ebay-sku-migrate-default. Fixing ebay_sku_migrate.py's
`migrate_cfg.get('enabled', True)` default to `False` to match the
documented "disabled by default" behavior. Verified live config has
`enabled` explicitly set to `true` so no behavior change today. Small,
single-line change; running pytest with PYTHONPATH override for
acceptance, then writing result manifest and committing.
