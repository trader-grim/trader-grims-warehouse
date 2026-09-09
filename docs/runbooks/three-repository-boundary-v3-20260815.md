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

## GitHub publication (off-site mirror)

Both the application source and the plan vault publish to GitHub through
`tgw-release`, each with its own **per-repo deploy key** (never a personal
account) served by `tgw-github-agent.service`:

| repo | wrapper | ssh alias | deploy key |
|---|---|---|---|
| application source | `/usr/local/bin/tgw-source-git {status,fetch,dry-run,publish}` | `github-tgw-app` | `/var/lib/tgw-release/.ssh/trader-grims-warehouse_deploy_ed25519` |
| plan vault | `/usr/local/sbin/tgw-plan-git {status,fetch,dry-run,publish}` | `github-tgw-plan` | `/var/lib/tgw-release/.ssh/tgw-plan_deploy_ed25519` |

Both wrappers are **fast-forward-only** (client-side; the vault has no server-side
broker). `tgw-publish.timer` runs `/usr/local/sbin/tgw-publish` every ~15 min as
`tgw-release`, publishing both when local `main` is ahead — so **landing on
`main` implies publishing** (coding-workflow two-gates). It is idempotent.
`tgw doctor` `source.github-publish` WARNs (never FAILs) if a mirror falls
> 20 commits / 3 days behind — a stalled timer cannot lapse unnoticed.

Install: `scripts/install-tgw-plan-publish prepare` (keygen + units), register
the printed public key as a **write-enabled deploy key** on `trader-grim/tgw-plan`
+ set branch protection "block force pushes" (NOT "require linear history" — the
vault stitches merges), then `scripts/install-tgw-plan-publish activate`.

### GitHub branch protection (both repos)

Belt-and-suspenders only — the real protection is the local FF-only brokers +
`main_ref_guard`, which is why a mis-set or missing GitHub ruleset never blocks
work. Target state on **both** `trader-grim/trader-grims-warehouse` and
`trader-grim/tgw-plan`: one ruleset targeting the default branch with **Block
force pushes** enabled, Enforcement = Active. Do **not** enable "Require linear
history" (both repos carry merge commits). A GitHub Ruleset with no target
silently does nothing ("does not target any resources").

History (2026-09-08): the `tgw-plan` deploy key was briefly added to
`trader-grims-warehouse` by mistake (removed), and during cleanup the
`trader-grims-warehouse` `main` ruleset was deleted. Recreate it per above when
convenient; nothing depends on it.

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
