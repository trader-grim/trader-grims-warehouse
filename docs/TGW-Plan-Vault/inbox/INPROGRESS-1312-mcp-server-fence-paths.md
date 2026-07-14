# In progress: todo #1312 mcp-server-fence-paths

Working in isolated worktree
`/opt/TGW/var/worktrees/1312-mcp-server-fence-paths` on branch
`todo/1312-mcp-server-fence-paths` (off `catio-nix-0.0.1-alpha`).

Task: fix `src/tgw/mcp_server.py`'s `tgw_get_item()` and `tgw_enqueue()`
to stop constructing ItemData paths inline
(`cfg['itemdata_root']/sku/f'{sku}.json'`) and instead use
`items.get_item()` / `items.sku_json()` + `resolver.find_current_sku()`
alias fallback, per packet
`docs/TGW-Plan-Vault/plan/packets/1312-mcp-server-fence-paths.md`.
Status: DONE. `tgw_get_item()` now calls `items.get_item(cfg, sku)`
(catching `FileNotFoundError` -> same not-found error shape); `tgw_enqueue()`
now uses `items.sku_json(cfg, sku)` + `resolver.find_current_sku()` alias
fallback before concluding not-found. New tests added for renamed-SKU
resolution in both tools. Full offline suite: 2179 passed, 1 skipped.
See `docs/TGW-Plan-Vault/plan/packets/results/1312-RESULT.md` for the
result manifest.
