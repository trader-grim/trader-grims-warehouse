# TGW three-repository boundary — v3 (2026-08-15)

**Supersedes operational use of:** `three-repository-boundary-v2-20260815.md`.
The older document remains historical evidence and is not overwritten.

TGW has exactly three source-control authority domains:

| Authority | Canonical host and path | Upstream |
|---|---|---|
| Application source | `tgw-lib:/opt/TGW/tgw-lib/src/trader-grims-warehouse` | `trader-grim/trader-grims-warehouse` |
| Plan | `tgw-lib:/opt/TGW/library/plans` | standalone Plan repository |
| Production NixOS flake | `tgw-prod:/home/db/tgw-flake` | `trader-grim/tgw-flake` |

These histories must never be merged. The application repository must not contain
`docs/TGW-Plan-Vault`; Plan consumers use `/opt/TGW/library/plans` or an immutable
approved snapshot selected from it. The flake may pin an application revision, but
must not contain or become the application repository.

## Production retirement state

The legacy checkout `tgw-prod:/opt/TGW/src/trader-grims-warehouse` was preserved as
an all-ref bundle and byte-exact recovery copy on `tgw-lib`, verified, removed from
the production host, and replaced by a root-owned read-only sentinel. It must not be
recreated or used as a source fallback. Production application processes execute
from immutable generations selected through `/opt/TGW/current`.

## Worktrees and scratch

Harness worktrees live below `/opt/TGW/tgw-lib/actors/<actor>/worktrees/` and share
the canonical application Git object store. The primary checkout may be dirty and
must not be reset to promote another branch. Large test/build scratch belongs on a
bounded disk-backed path below `/opt/TGW`; `/tmp` is RAM-backed shared infrastructure
and must not hold source trees or large test artifacts.

## Verification before release

1. Verify the standalone Plan root and immutable approved commit independently.
2. Resolve the application candidate to an exact commit/tree from the canonical Git
   store; do not use a production checkout or an embedded Plan copy.
3. Run the candidate's focused and full test gates from a clean worktree.
4. Promote `main` only by expected-old-value ref update after review.
5. Install an exact Git archive as a new immutable generation; never copy a mutable
   worktree into `/opt/TGW/current`.
6. Reconcile the production flake separately and switch it only from its own clean
   `master` branch.
7. Verify every service reports the installed generation and that both Syncthing
   instances retain their GUI-managed topology.

The application, Plan, and flake each retain their own credentials, review history,
release evidence, and rollback path.
