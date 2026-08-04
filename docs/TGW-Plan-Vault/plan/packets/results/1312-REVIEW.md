status: cleared
reviewer: Claude (main session)
todo: #1312
pp_ref: PP-COHESION-001 (matches)

Diff matches packet spec exactly: tgw_get_item() now delegates to
items.get_item(cfg, sku), catching FileNotFoundError for the existing
error shape (additive _images/_videos keys left in, per spec).
tgw_enqueue() now uses items.sku_json() plus find_current_sku() alias
fallback instead of an inline path/exists check. Local imports (from tgw
import items) rather than module-level -- acceptable, matches the
packet's flexibility on import style and this file's existing mixed
pattern. Scope: mcp_server.py + its tests + result manifest only. Live
evidence: 21 targeted tests passed, 2179 passed/1 skipped full suite,
worktree module resolution confirmed. No invariant violations, no
out-of-scope findings. Cleared for stitch.
