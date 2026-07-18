# INPROGRESS: todo #1458 (PP-HERMES-EA-001) — Aider task_slug gate + preflight seam

Working in worktree `/opt/TGW/var/worktrees/1458-aider-task-slug-gate` on branch
`todo/1458-aider-task-slug-gate`, off `catio-nix-0.0.1-alpha`.

Plan:
1. (done) Live-verified `claude mcp list` — tgw-aider connects fine as `db`, no
   529 outage blocking it right now.
2. Fix `aider_run_task`'s `task_slug=''` fallthrough in
   `src/tgw/aider_mcp_server.py` — make task_slug required, reject empty/missing
   with a clear error (option a from packet, preferred).
3. Add a preflight seam surfacing Plan Vault inbox count/names + `tgw plan check`
   warnings into the aider task's initial context before invoking aider.
4. Add tests, run full pytest suite.
5. Write result manifest.
