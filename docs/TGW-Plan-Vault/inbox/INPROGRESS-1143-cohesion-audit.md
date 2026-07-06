# INPROGRESS — todo #1143: full-codebase cohesion+correctness audit

Staged, per-subsystem cohesion+correctness audit via the Workflow tool (PP-* backlog
catch-up from CLAUDE.md's code-review cadence rule).

## Status (2026-07-05)

`workers/` subsystem slice DONE: 25 files, 5 groups, 60 agents, ~2.3M tokens.
17 findings confirmed (2-of-3 adversarial verify), 1 refuted/dropped. Full report at
`docs/TGW-Plan-Vault/dev-workflow/research/RESEARCH-1143-workers-audit.md`. Each of the
9 correctness bugs spun off as its own todo (#1162-#1170); the 8 lower-severity
invariant/cohesion findings batched into one todo (#1171).

## Remaining subsystems queue (resumable, run opportunistically)

apis/ebay/, http_server.py, queue/state-machine, scripts/, nix flake.

No action in progress right now -- next session can pick the next subsystem or work
the spun-off correctness-bug todos (#1162-#1170) directly.
