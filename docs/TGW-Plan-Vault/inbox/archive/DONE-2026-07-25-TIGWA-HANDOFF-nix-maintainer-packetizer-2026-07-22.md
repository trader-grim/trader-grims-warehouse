# Handoff: bounded Nix-maintainer packetizer (draft-only first run)

Message ID: `TIGWA-NIX-MAINTAINER-PACKETIZER-2026-07-22`
Recipient: Claude
From: Tigwa, at Dave's direction
Status: implementation handoff; no live-flake authorization

## Dave's direction

When Claude returns tomorrow morning, run forthcoming flake-change requests through the new bounded packetizer and observe whether its output stays correct and tightly scoped. Start draft-only. Do not spend API credit or run the model tonight.

## Product delivered

A first pipeline stage now compiles an explicitly scoped, read-only source packet for a Nix-maintenance request. It exists to stop a low-cost model from receiving a whole agent transcript / broad flake dump and to make its output auditable.

- Worktree: `/opt/TGW/var/worktrees/aider-20260722-nix-request-packetizer`
- Branch: `task/aider-20260722-nix-request-packetizer`
- Commit: `caa3e0d910750a6ffd44959763584b1b1bcb4161`
- Commit subject: `add bounded nix request packet service`
- Not pushed or merged.

Files:
- `src/tgw/nix_request_packet.py`
- `tests/test_nix_request_packet.py`
- `pyproject.toml` (`tgw-nix-request-packet` entry point)

Contract:
- Requires request ID, host (`a1131` or `tgw-prod`), goal, explicit target-file list, and named allowed checks.
- Caps file count and source-file size; embeds only named UTF-8 files plus SHA-256 provenance.
- Rejects absolute/traversal paths, hidden/sensitive-looking paths, duplicate/non-file/oversized targets, and unsupported checks.
- Allows only `nix-flake-check` and `dry-activate` as named verification intentions.
- The generated packet expressly authorizes analysis/proposed diff only. It does not authorize a flake edit, commit, push, `nixos-rebuild switch`, restart, or remote action.

## Evidence already run

- Focused test suite: 7 passed.
- Ruff: passed.
- `git diff --check HEAD~1..HEAD`: passed.
- Synthetic CLI canary produced a packet and preserved the fixture source SHA-256 exactly.

## Known runner seam (do not paper over)

In this worktree, `flake.lock` is a symlink to `/home/db/tgw-flake/flake.lock`; the `tgw` service account cannot read that target. Repository-root pytest collection therefore fails before test execution. The focused packetizer tests were run from a tgw-owned external test directory with the worktree source on `PYTHONPATH`.

This is an environment/configuration seam, not a reason to broaden permissions. Any hosted follow-up needs an explicit host-local flake-root/output configuration and should preserve the present no-secret/no-arbitrary-path boundary.

## Requested tomorrow: bounded observation run

1. Review the commit and packet contract.
2. Use a real, current flake request with the smallest explicit source-file set.
3. Generate the packet, confirm its source hashes/scope, and use it for a draft-only low-cost-model pass (DeepSeek V4 Flash direct is the proposed candidate).
4. Return the proposed diff, named verification plan, model output/token-use evidence if available, and every uncertainty/blocker.
5. Check that the model neither invents files nor broadens scope.

## Explicit non-authorizations

- No flake mutation, commit, push, `switch`, service restart, or remote action from this handoff.
- No provider/model configuration change or API use tonight.
- Do not resolve the `/home/db/tgw-flake/flake.lock` access seam by widening account access.
- Dave remains the authority for any live execution after the draft-only evidence is reviewed.
