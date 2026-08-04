# In progress: todo #1484 — PP-RUNNERCOMMS-001 mailbox mechanism

Working in worktree `/opt/TGW/var/worktrees/1484-mailbox-mechanism` on branch
`todo/1484-mailbox-mechanism` (base: `catio-nix-0.0.1-alpha`).

Building per `pp/PP-RUNNERCOMMS-001.md`'s "mailbox design" section: `tgw mailbox send
<actor> "<message>"` CLI (new `cmd_mailbox_send` in `api.py`), a Claude Code skill
wrapping it, an MCP tool (`tgw_mailbox_send`, respecting `TGW_MCP_READONLY`), and
generalizing `.claude/hooks/session-start-briefing.py`'s inbox-count logic (currently
hardcoded to `inbox/claude`) to accept any actor. Naming convention reverse-engineered
from real inbox notes: `<FROM-ACTOR>-<TYPE>-<slug>-<date>.md` inside `inbox/<to-actor>/`,
markdown header `# <Type>: <title>` + `**From:**` metadata line.

Status: COMPLETE. Implemented `cmd_mailbox_send` in `src/tgw/api.py` + `tgw
mailbox send` CLI subcommand, `tgw_mailbox_send` MCP tool in
`src/tgw/mcp_server.py` (readonly-gated like `tgw_enqueue`/`tgw_add_suggest`),
the `tgw-mailbox-send` Claude Code skill, and generalized
`.claude/hooks/session-start-briefing.py` (own actor via `TGW_HOOK_ACTOR`,
full listing; every other actor's mailbox surfaced as a count only, never
filenames). All live-tested (CLI, MCP call, hook subprocess with a second
actor) against a throwaway scratch Plan Vault, never against the real
inboxes. New tests: tests/test_mailbox.py, tests/test_session_start_briefing_hook.py,
additions to tests/test_mcp_server.py. Full `pytest -q` run: 2391 passed, 2
pre-existing unrelated failures (test_invariant_c12_field_set_accessors.py,
line-number drift against http_server.py, confirmed present on the
unmodified base commit too — filed as todo #1506/PP-LISTEDITOR-001).
See result manifest at plan/packets/results/1484-mailbox-mechanism-RESULT.md.
