Working todo #1274 (PP-COHESION-001) in worktree
`/opt/TGW/var/worktrees/1274-config-path-safety-validation` on branch
`todo/1274-config-path-safety-validation`: hardening `config.sku_dir()` and
`config.location_dir()` against path-traversal/absolute-path-override input
per the packet spec (shared `_safe_segment()` validator with a strict
alphanumeric/`_.-` allow-list, verified against real live SKU/location
values first). Applying the fix exactly as specified in
`docs/TGW-Plan-Vault/plan/packets/1274-config-path-safety-validation.md`,
then running the offline suite with PYTHONPATH pointed at this worktree's
`src/`, then writing the result manifest.
