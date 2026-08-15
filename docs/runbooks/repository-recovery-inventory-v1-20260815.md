# TGW repository recovery inventory — v1 (2026-08-15)

**Status:** preserved and reconstructed; canonical checkout cutover pending review.

This inventory records the exact recovery sources and clean candidates created
while separating the application repository from the production NixOS flake.
It is deliberately a new runbook and does not replace any earlier operational
record.

## Preserved sources

No branch was deleted, force-pushed, reset, or made canonical during recovery.

| Recovery source | Preserved object | SHA-256 or Git identity |
|---|---|---|
| Current application refs | `/opt/TGW/tgw-lib/preservation/repository-separation-20260815/application-all-refs.bundle` | `711092a96030e2a176a37deccaae4e67e70076d1ce42324ddf15a369a53945c5` |
| Current dirty application checkout | `refs/preservation/repository-separation-20260815/dirty-worktree` | commit `3a25ebd2e25e8df7bea45abef76022e06e3d4e48`, tree `72f174aaf06a229b958bcabdd3016234939e7740` |
| Btrfs recovery checkout refs and dirty files | `/opt/TGW/tgw-lib/preservation/repository-separation-20260815/btrfs-at-tgw-all-refs-and-dirty.bundle` | `77f3376e387ac627308a50edb21faf370c50051976d869215b6177141929042e` |
| Btrfs dirty checkout snapshot | same bundle | commit `16062bb0fd70bdbe977c28e6fdb5f2f9f55649a8`, tree `16014917e305de0180ba54cfeee3cfe7934c8ec1` |
| GitHub `tgw-flake` refs | `/opt/TGW/tgw-lib/preservation/repository-separation-20260815/tgw-flake-remote-all-refs.bundle` | `4e051b1f261a1cf5783eb5124c50a55afa21d62f3e6d514f7bbdf729da425dc9` |

The bundle files are root-owned, read-only preservation artifacts. Bare mirrors
in the same directory are inspection aids; the bundles are the portable
recovery records.

## Btrfs finding

`btrfs subvolume list -a /opt/TGW` reports one child subvolume:
`/opt/TGW/@tgw` (subvolume ID 256). `btrfs subvolume list -s /opt/TGW`
reports no read-only snapshots. Therefore `@tgw` is a writable historical
subvolume, not an immutable snapshot and not a canonical repository.

Its application checkout had committed HEAD
`812f691184528337a3f4433475962963ba6b8e5f`; that commit is already an
ancestor of the current application history. Its distinct dirty work and refs
are retained in the Btrfs recovery bundle above. They must be reviewed and
cherry-picked deliberately if wanted; they must not be copied wholesale into a
clean candidate.

## Clean candidates

| Repository | Local checkout | Candidate ref | Tested reconstruction commit |
|---|---|---|---|
| Application | `/opt/TGW/w/application-clean-v1` | `repair/application-clean-v1` | `741c9d8776cea8fcda1e028c462a0850637e6dc4` |
| Production flake | `/opt/TGW/tgw-lib/src/tgw-flake` | `repair/flake-clean-v1` | `21db8abde37133170807ed30ff1ca758e4c90d38` |

The application candidate is based on GitHub application `main`
`6f2d7ef579d6638c7b93ea53cd06e9674e6c08ad`. Its full tracked test run was
`4057 passed, 5 skipped`; repository-boundary tests and Ruff also passed.
The candidate branch may contain later runbook-only successors, including this
inventory. Resolve and record its exact tip when review begins; do not encode a
self-referential containing-commit claim in this file.

The flake candidate is based on GitHub flake `master`
`bcb4bdd10c0ade3da12efe557c849bd284f3dbf7`. It restores the application as
a separate locked `tgw-src` input and contains no application source tree. Its
boundary tests, JSON checks, Ruff, and Git object checks passed. No local Nix
binary was available, so this record makes no Nix evaluation or build claim.

The flake candidate has been published and read back at
`refs/heads/repair/flake-clean-v1`. Remote `master` remains unchanged. The
application candidate is still local because its dedicated GitHub deploy key
has not yet been registered with the application repository.

## Access and remaining cutover sequence

Installed commands are candidate-only:

- `/usr/local/bin/tgw-source-git` can publish only
  `repair/application-clean-v1`.
- `/usr/local/bin/tgw-flake-git` can publish only
  `repair/flake-clean-v1`.
- Neither wrapper can push `main`, `master`, production, arbitrary refs, or a
  force update.

To continue without mixing repositories:

1. Register `/etc/ssh/tgw_github_app.pub` as a write-enabled deploy key on
   `trader-grim/trader-grims-warehouse` only. Expected fingerprint:
   `SHA256:YXP8QdZ6BIkp11hN/f9wzzdft74oas20GJAPjUdy/m0`.
2. Publish and read back `repair/application-clean-v1` using the fixed source
   wrapper.
3. Review both candidates independently.
4. Update the production flake candidate to the reviewed application successor
   and regenerate its lock using an admitted Nix environment.
5. Promote repository branches through separate reviewed changes.
6. Only after promotion, replace canonical checkouts with fresh clean clones.
   Retain the bundles and dirty checkout until post-cutover verification passes.

Do not point either existing dirty checkout at the other repository, rename a
branch to make it appear canonical, merge the two histories, or use the flake
credential to publish application source.
