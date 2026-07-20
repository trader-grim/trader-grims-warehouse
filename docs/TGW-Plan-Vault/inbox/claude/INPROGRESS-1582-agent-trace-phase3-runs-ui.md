# In progress: todo #1582 — PP-AGENTTRACE-001 Phase 3 (/form/runs UI)

tgw-coder executing in isolated worktree
`/opt/TGW/var/worktrees/1582-agent-trace-phase3-runs-ui` on branch
`todo/1582-agent-trace-phase3-runs-ui`, based off `catio-nix-0.0.1-alpha`.

Building `GET /form/runs` HTTP UI page per packet
`docs/TGW-Plan-Vault/plan/packets/1582-agent-trace-phase3.md`: session-cookie
auth (existing `_session_guard` middleware), `_render_runs_html()` matching
`_render_todos_html()`'s query->render->200-even-on-error shape, client-side
filter by agent_type/status/search, transcript_path shown as escaped text
(no file-serving route exists yet). Depends on Phase 2's `list_agent_runs()`
in `state_machine.py` (merged).
