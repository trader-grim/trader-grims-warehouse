# In progress: todo #1520 — plan_brief refactor (tgw-coder)

Working on branch `todo/1520-plan-brief-refactor`, worktree
`/opt/TGW/var/worktrees/1520-plan-brief-refactor` (never the shared
checkout). Implementing Tigwa's follow-up refactor for
`tgw_get_plan_brief` v1 (PP-KNOWLEDGE-001/#1439): moved the deterministic
parser/retrieval logic out of `src/tgw/mcp_server.py` into a new pure
helper `plan_brief(cfg, pp_ref)` in `src/tgw/plan_render.py`; the MCP tool
now delegates to it. Paths derived from `cfg['plan_master_path']` /
`cfg['plan_vault_path']` — no hard-coded Plan Vault root left in
mcp_server.py. Linked `plan/pp/<PP>.md` detail documents are now
metadata-only (path/status/sha256/bytes) per spec item 4 — content is
never inlined, a deliberate behavior change from v1 for that one field
only (Master Plan section/canonical_source retrieval is unchanged, verified
byte-identical against the pre-refactor implementation).

Status at write time: code + tests done, full pytest suite run (2538
passed, 1 skipped, 0 failures), byte-identical section/hash verification
done against Tigwa's live evidence. Writing result manifest next at
`docs/TGW-Plan-Vault/plan/packets/results/1520-RESULT.md`.
