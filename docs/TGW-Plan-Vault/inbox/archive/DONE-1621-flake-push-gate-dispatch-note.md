# In progress: todo #1621 (PP-FLAKEGATE-001) — flake mutation gate

Live incident (2026-07-20/21): `nix-flake-maintainer` committed AND pushed
`4adb145` (far2l addition, todo #1620) to `origin/master` on `~/tgw-flake`
without Dave's explicit push confirmation. Root cause found this session:
the 2026-07-20 "batch the mutating calls" change to
`.claude/agents/nix-flake-maintainer.md` chains `git add && git commit &&
git push` into one compound Bash call per host (done to cut 5+ approval
prompts down to ~1) — that batching, combined with this session running in
Auto Mode (permission prompts suppressed) and the confirmed-broken
upstream hook-for-subagents bug (anthropics/claude-code#69260), meant
nothing actually gated the push. Far2l itself has been reverted
(commit reverting 4adb145, pushed to origin/master per Dave's direct
instruction) — never actually applied to a1131's running system.

**Dave's direction:** don't patch the prompt/hook path (proven unreliable
twice now) — build a state-machine-centric gate instead, reusing the same
pattern as `enqueue_job()`/`queue_jobs` and `ebay_publish`'s manual-trigger
pattern (`tgw publish <sku>`, see `src/tgw/api.py:751`). Design:

- `nix-flake-maintainer` commits locally, then calls a new CLI command
  (`tgw flake request-push` / `tgw flake request-switch`) that enqueues a
  `queue_jobs` row (queue_name `flake_mutation`, entity_id=commit sha,
  payload={repo, host, kind, summary}) instead of running `git push` /
  `nixos-rebuild switch` directly.
- A separate, explicitly human-triggered command (`tgw flake push <id>` /
  `tgw flake switch <id>`) is the ONLY thing that actually executes the
  push/switch — modeled on `tgw publish`'s "approve and publish now"
  pattern. No agent calls this command as part of its own procedure.
- Update `.claude/agents/nix-flake-maintainer.md` Step 2 to request instead
  of execute; remove direct `git push`/`nixos-rebuild switch` from its
  procedure entirely.
- New invariant (next available E-number) + detector: any push/switch on
  `~/tgw-flake` not backed by a matching executed `flake_mutation` job is a
  finding.
- **Test case: re-add far2l to a1131 via this new path**, end to end,
  verified live (`far2l --version` on a1131).

## Status
- far2l revert: dispatched to nix-flake-maintainer, in flight.
- CLI mechanism build: not yet dispatched — need to write the actual
  work-packet spec and hand to `tgw-coder` (this is `src/tgw/` app code,
  routes through that profile per invariant E12, not editable directly from
  main session).
- Master plan: PP-FLAKEGATE-001 not yet written up in
  `TGW-Master-Plan.md` — do that alongside/before dispatching the packet.

## If interrupted
Read this note, check `tgw todo` for #1621's current state, check whether
a `tgw-coder` branch `todo/1621-*` already exists under
`/opt/TGW/var/worktrees/` before redispatching.
