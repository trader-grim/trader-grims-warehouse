# TGW three-repository boundary and legacy checkout retirement — v2 (2026-08-15)

**Supersedes:** `repository-separation-v1-20260815.md` for repository placement.
The v1 record remains historical evidence and is not overwritten.

## Canonical repositories

TGW has exactly three source-control authority domains:

| Authority | Host | Canonical path | Branch convention |
|---|---|---|---|
| Application source | `tgw-lib` | `/opt/TGW/tgw-lib/src/trader-grims-warehouse` | reviewed application branch promoted to `main` |
| Plan | `tgw-lib` | `/opt/TGW/library/plans` | immutable approved Plan commit; evidence may advance repository `HEAD` |
| Production NixOS flake | `tgw-prod` | `/home/db/tgw-flake` | `master` |

Application workers receive individual worktrees backed by the shared application
Git store. They do not clone from, execute from, or fall back to a checkout on
`tgw-prod`. Plan material is never copied into the application repository as a
second authority. The production flake may pin a reviewed application source, but
that dependency does not merge either Git history.

## Forbidden legacy checkout

`tgw-prod:/opt/TGW/src/trader-grims-warehouse` is a legacy monolithic checkout. It
is not a fourth repository authority and must not remain as a usable working tree.

Retirement is ordered and fail-closed:

1. Observe its exact commit, branch, remotes, worktree status, submodules, and file
   identities without modifying it.
2. Preserve committed refs as a Git bundle and preserve every dirty/untracked byte
   in a content-addressed recovery artifact on `tgw-lib`.
3. Re-read and verify the transferred hashes on `tgw-lib`.
4. Prove that no live unit, wrapper, worker configuration, Plan consumer, or release
   command refers to the legacy path.
5. Remove the checkout from `tgw-prod`. Do not rename or quarantine it elsewhere on
   that host, because a discoverable checkout can become an accidental fallback.
6. Install a root-owned non-directory sentinel at the old path's parent binding that
   makes attempts to recreate the checkout explicit and observable.
7. Verify the installed platform and worker provisioning using only the three
   canonical repositories above.

Deletion is authorized only after steps 1–4 prove preservation and zero live use.
If access or proof is unavailable, keep the retirement status `HOLD`; do not use
the legacy checkout to make progress.

## Development worktree contract

- The shared application repository is owned by the development access boundary
  (`db:tgw-coders` in the current installation).
- Each harness uses `/opt/TGW/tgw-lib/actors/<actor>/worktrees/<task>`.
- Worktrees share the canonical Git common directory; independent clones are
  recovery inputs only and are retired after their refs are admitted.
- Requests bind a full source commit. An omitted source commit must never select a
  production checkout or an unrelated evidence branch.
- Test source stays in the worktree. Large scratch uses a bounded disk-backed task
  directory, never the shared RAM-backed `/tmp` filesystem.

## Current reconciliation checkpoint

As of this successor:

- the reconciled application branch is stored in the shared `tgw-lib` Git store;
- coding defaults point to the shared application repository;
- runtime Python defaults point Plan Vault access to `/opt/TGW/library/plans`;
- the production legacy checkout has not yet been retired because the admitted
  Codex SSH key is currently rejected by `tgw-prod`;
- no alternate access path is authorized as a workaround.

This checkpoint is not an installation-complete receipt.
