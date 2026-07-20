# In progress: todo #1580 — PP-AGENTTRACE-001 Phase 1

Working in isolated worktree `/opt/TGW/var/worktrees/1580-agent-trace-phase1` on
branch `todo/1580-agent-trace-phase1`. Building the `agent_runs` Postgres table
(in-code self-apply DDL in `state_machine.py`, schema.sql copy for bootstrap docs),
`start_agent_run()`/`end_agent_run()` functions, `tgw trace start`/`tgw trace end`
CLI subcommands, and `archive_transcript()` helper in `logging.py` per packet
`docs/TGW-Plan-Vault/plan/packets/1580-agent-trace-phase1.md`. Unit tests required.
Result manifest goes to `docs/TGW-Plan-Vault/plan/packets/results/1580-RESULT.md`.
