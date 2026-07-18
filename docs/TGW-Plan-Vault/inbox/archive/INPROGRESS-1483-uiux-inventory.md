# In progress: #1483 PP-UIUX-001 Phase 1 UI inventory + tgw-mapping

Executing todo #1483 (PP-UIUX-001 Phase 1) on branch `todo/1483-uiux-inventory` in worktree
`/opt/TGW/var/worktrees/1483-uiux-inventory`. Task: catalog every web UI page (tgw-http
`http_server.py` routes returning HTML) and any real Flutter screens, map each to the
`/api/*` endpoint(s) or CLI it calls, and refresh `docs/TGW-Plan-Vault/reference/TGW-HTTP-API.md`
to be accurate as of 2026-07-17 (live-verified against actual route table + actual frontend
fetch() calls, not old doc claims — invariant C11). Documentation-only task, no code changes.
Scoped to Phase 1 inventory/mapping; not attempting redesign/fix of any mismatch found (filed
as a todo instead per contract). Started 2026-07-17.
