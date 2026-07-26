# Clarification: #1692 flake-input remediation process

**From:** tigwa
**To:** claude
**Date:** 2026-07-25
**Re:** `TIGWA-RESPONSE-consolidated-nix-source-merge-status-2026-07-25.md`

My prior wording, “have the flake/input owner correct,” was intentionally not an instruction for you to directly edit the flake, but it was too ambiguous. The intended route is the current **PP-FLAKEGATE-001 / E17 process**.

- The failing development-shell contract belongs to the pinned `tgw-flake` input, so #1692 remains the bounded defect/gate.
- `nix-flake-maintainer` is the constrained flake executor for any approved `~/tgw-flake` change; it is not a bypass around the new process.
- Claude’s role here is planning/review and evidence, not direct flake mutation, push, or switch.
- After Dave approves the bounded repair, the maintainer performs the guarded local change/commit and required checks. For a source-dev-shell repair, the required acceptance evidence includes the exact clean source-worktree command:

```text
nix develop -c pytest
```

- Any Git push is requested through `tgw flake request-push`; the agent stops there. Dave performs the actual push and the human-only `tgw flake mark-executed` receipt. No NixOS switch is implied by this dev-shell-only correction.
- Once the fleet input is actually advanced, the source-adapter branch needs its lock update and a fresh independent merge/test review through the normal source-repository lifecycle before it can be merged.

So: use the new process, with `nix-flake-maintainer` as its narrowly governed executor—not a direct-Claude flake repair and not an agent-run push/switch.
