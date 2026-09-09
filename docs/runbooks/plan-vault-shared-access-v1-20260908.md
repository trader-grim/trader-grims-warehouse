# TGW plan vault — shared group access — v1 (2026-09-08)

The plan vault (`/opt/TGW/library/plans`, GitHub `trader-grim/tgw-plan`) is a
group-shared git repository, written by any `tgw-coders` session that lands a
Plan / PP / concept-registry commit (same as the application source repo, but
via a different remote and with no fast-forward-only `main` broker — see
`three-repository-boundary-v3-20260815.md`).

## Required permission config

The repository must satisfy the git "shared repository" contract for the
`tgw-coders` group:

| what | required state |
|---|---|
| `.git/config` | `core.sharedRepository = group` |
| `.git` group | `tgw-coders` |
| `.git/objects/**` and `.git/worktrees/**` directories | group-writable + setgid (`g+rwxs`) |
| object files | group-readable (`0444` is fine — they are immutable) |

`core.sharedRepository = group` is the load-bearing setting: with it, git
creates every new object, fanout directory, and worktree group-writable and
setgid, so any `tgw-coders` member can add objects. Without it, git uses the
caller's umask and objects land private to whoever created them.

## Why (incident, 2026-09-08)

A root operation on 2026-09-07 (a Context repair) left 24 of the 256
`.git/objects/??` fanout directories owned `root:tgw-coders drwxr-sr-x` — no
group write. `core.sharedRepository` was unset, so nothing corrected them.
Later commits succeeded or failed **probabilistically**: a commit lands only if
none of its new objects hash into one of the root-owned directories
(≈ 9% per object). `5a3487c` (the Catio concept layer) landed by luck; the
2-record follow-up could not, and `resolve("ratter")` returned `UNKNOWN` until
the directories were fixed.

## Fix / restore

Run as a `tgw-release`/`db` operator or root:

```sh
cd /opt/TGW/library/plans
git config core.sharedRepository group
sudo find .git/objects .git/worktrees -type d -exec chmod g+rwxs {} +
sudo chgrp -R tgw-coders .git
```

Idempotent. Apply after any root-run operation on the vault (a repair, a
`git gc`, a manual clone/restore).

## Doctor coverage — `access.plan-vault-git`

Closed 2026-09-08 (LEAF-11 ratter reconciliation, first leaf). `tgw doctor` now
runs **`access.plan-vault-git`** (`src/tgw/doctor_cli.py` `check_plan_vault_git`):

- asserts `core.sharedRepository = group`, `.git` group = `tgw-coders`, and
  every `.git/objects` / `.git/worktrees` directory is group-writable + setgid;
- FAIL hands the operator the idempotent fix above (it never writes);
- absent vault → UNKNOWN (not every host carries `/opt/TGW/library/plans`).

It is **not** auto-repairable — the fix needs root/`tgw-release` on the vault,
which the operator runs (or, once ratified, a `%tgw-operators` member — the fix
is folded into **PP-ROLES-001 WU-3** and granted through **WU-9**).

`access.unix-group` / `repair unix-git-access` still covers only
`/opt/TGW/tgw-lib/src/trader-grims-warehouse` and its worktrees.

## Publication to GitHub

`trader-grim/tgw-plan`, `github` remote. `main` push is a deliberate operator
step (no automated push, no fast-forward-only broker as the source repo has).
As of 2026-09-08 the GitHub `main` was 5 days / ~30 commits behind local — the
push cadence is manual and had lapsed.
