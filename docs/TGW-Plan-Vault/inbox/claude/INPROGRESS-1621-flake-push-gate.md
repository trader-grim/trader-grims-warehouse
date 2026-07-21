# In progress: todo #1621 (PP-FLAKEGATE-001) — flake mutation gate (tgw-coder execution)

Executing in isolated worktree `/opt/TGW/var/worktrees/1621-flakegate` on
branch `todo/1621-flakegate`, off `catio-nix-0.0.1-alpha`.

Building `tgw flake` subcommand group (request-push, request-switch, queue,
show, mark-executed, audit) per the dispatched packet. Per the packet's
explicit spec, the tgw CLI NEVER executes git push / nixos-rebuild switch
itself — `mark-executed` only records that a human did it by hand. (Note:
this differs slightly from the master-plan section's earlier phrasing of
`tgw flake push <id>` / `tgw flake switch <id>` as executing commands —
flagging as a deviation-from-earlier-design in the result manifest, but
following the more detailed/explicit packet spec as authoritative per
Prime Directive 3.)

Also updating `.claude/agents/nix-flake-maintainer.md` and adding
invariant E17 to `reference/invariants.md`.

If interrupted: check `git log` on this branch/worktree for progress,
check `tgw todo brief 1621` for status.
