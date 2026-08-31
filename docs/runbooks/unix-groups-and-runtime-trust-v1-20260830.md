# TGW Unix groups and coding-runtime trust — v1 (2026-08-30)

**Owner:** shared platform
**Applies to:** `tgw-lib` (development host); groups and the coding runtime
exist only on tgw-lib, never on `tgw-prod`
**Companion runbooks:** `tgw-root-effect-recovery-v1-20260829.md`,
`universal-coding-database-v1-20260829.md`,
`shared-source-access-v4-20260815.md`

This runbook is the canonical registry of TGW Unix groups and the exact trust
invariants of the local coding runtime. It exists because both were previously
documented only implicitly in code, and the mismatch between them (a group-
restoration sweep that changed the selector lock's mode) silently deadlocked
every privileged Doctor repair.

## 1. Permission model

- **Groups discriminate WHO, never restrict permissions.** Membership in a
  group is the entire access story for ordinary operations. There are no ACLs,
  no per-user artifacts, and no actor names in sudoers, policies, queues,
  roles, or service overrides.
- **Any-actor model:** every ordinary coding harness (deepseek, codex, claude,
  hermes, opencode, prime, hermaroid, tigwadev) is a member of `tgw-coders`
  and is interchangeable for ordinary coding work.
- **One privileged spine.** Only `/usr/local/sbin/tgw-root-effect` and the
  pinned `/usr/local/sbin/tgw-coding-bootstrap` hold host privilege, and only
  through password-protected sudo. There is no other privileged surface.
- **`db` is the operator's personal login**, not a role. It owns the runtime
  layout because the materializer runs as the operator; ownership by `db` is
  not a "db-only" policy, and the trust checks accept any process whose euid
  is the owner.

## 2. Group registry (as of 2026-08-30)

| Group | GID | Purpose | Members |
| --- | --- | --- | --- |
| `tgw-coders` | 983 | Actor abstraction for all coding harnesses and coding service accounts. Universal DB role `tgw_coding` via pg_ident peer map. | tigwadev, db, codex, opencode, claude, hermes, prime, deepseek, tgw-git, tgw-release, hermaroid |
| `tgw-access` | 987 | Operational surfaces: agent-exchange, toolchains, review-services, coding-logs, library, foreman-tick. | tigwadev, hermaroid, db, codex, claude, hermes, deepseek |
| `tgw-release` | 985 | Publication privilege boundary: application deploy key and the fixed `tgw-source-git` operations only. | db, codex, tigwadev, deepseek |
| `tgw-agent-exchange` | 986 | Agent-exchange surface (narrow). | tigwadev, hermaroid |
| `nix-users` | 981 | Nix daemon build access (platform). | codex, db, tigwadev, opencode, claude, hermes, prime, deepseek, hermaroid |

Service accounts (nologin unless noted): `tgw-git` (982, deploy key owner),
`tgw-release` (publication broker, member of `tgw-coders`), `codex` (1004,
implementation worker user), `claude` (1006, implementation worker user),
`tgw` (1003, legacy runtime). Worker users are members of `tgw-coders` and run
only their one declared unit; the unit override, not the user, declares the
executor.

Adding a harness: `usermod -a -G tgw-coders,<access groups> ACTOR` (root),
then update the `tgw-coders` pg_ident peer map per
`universal-coding-database-v1-20260829.md`. New groups apply only after the
actor's next login/session start.

## 3. Coding-runtime trust invariants

Root: `/opt/TGW/tgw-lib/coding-runtime`. `current` is a symlink
`releases/<generation>`; releases are immutable, root-owned, and never written
after install.

### 3.1 Operations directory and selector lock

`operations/` and its `.selector.lock` guard every runtime mutation
(materialization and every Doctor repair). Both are enforced by
`tgw.doctor_cli._runtime_selector_lock` and `tgw.release_installer`:

- `operations/` must be a real directory (not a symlink), owned by `db` (or
  the acting euid), with **no group/other write bits** (`mode & 0o022 == 0`).
- `.selector.lock` must be a regular file with **link count 1**, owned by
  `db` (or the acting euid), with mode **exactly `0o600`** (`-rw-------`).
- The lock must not change identity (dev/ino) between stat and flock.

**Failure mode observed 2026-08-30:** a group-restoration sweep chowned the
lock to `db:tgw-coders` and left it `0o640` (`-rw-r-----`). Every privileged
repair then failed with `runtime selector lock is unsafe`, and because every
repair takes the lock first, no repair could fix the lock itself.

**Fix (root or owner, one command):**

```bash
sudo chmod 600 /opt/TGW/tgw-lib/coding-runtime/operations/.selector.lock
```

Never apply a recursive `chmod`/`chown` to `coding-runtime/` — releases are
immutable and `operations/` has an exact mode contract.

### 3.2 Release layout

`releases/`, `operations/`, `receipts/`, `refusals/` and the root must be real
directories owned by the materializer (`db`), mode `0o750` or tighter, no
`0o022` bits. A release tree must match its Git commit exactly (mode, blob,
symlink), contain no writable files (`mode & 0o022 == 0`), no hard links
(`st_nlink == 1`), and no files owned outside the trusted set. `current` must
resolve inside `releases/`.

### 3.3 Same-filesystem rule (scratch and durable roots)

The repository worktrees live on the btrfs filesystem under `/opt/TGW`
(device 39 on the current host). Everything that interacts with Git
metadata, leases, reflinks, hard-linked pack components, or Doctor `st_dev`
trust checks must be on that same device:

- `/tmp` is **tmpfs** — RAM-backed, bounded, cleared on reboot. It is a
  deliberate pressure valve (see section 5) and is a **different filesystem**:
  never a workspace, cache, or durable root.
- `/var/tmp` and `$HOME` (ext2/3) are **different filesystems** from the
  btrfs worktrees — same-fs operations (hardlink, reflink, `os.link`,
  leases) fail across the boundary.
- The only same-filesystem scratch is under `/opt/TGW` — e.g.
  `/opt/TGW/var/tmp` (setgid `tgw-coders`, mode `0o2770`) and the designed
  durable roots `/opt/TGW/w` and `/opt/TGW/var/cache/tgw`.
- **Per-actor, never shared:** a shared scratch root is created by whichever
  actor ran first and fails for every other actor (2026-08-30, `/var/tmp/
  tgw-pytest` and `/tmp/tgw-plan-graph` both hit this). Test defaults are
  per-actor on the same filesystem: `TGW_TEST_DURABLE_ROOT` default
  `/opt/TGW/var/tmp/tgw-pytest-<actor>`, `TGW_PLAN_GRAPH_RUNTIME` default
  `/opt/TGW/var/tmp/tgw-plan-graph-<actor>`; both env vars still override for
  CI.

### 3.3 Launcher surface

`/usr/local/bin/tgw` and `/usr/local/sbin/tgw-operator` are fixed launchers:
mode `0o555`, owner `db`, `tgw-coders` group, link count 1, hash matching the
release source. `/usr/local/sbin/tgw-coding-bootstrap` is root:root pinned.
`/opt/TGW/tgw-lib/bin/tgw-*` executors resolve through the runtime `current`.

## 4. Doctor: diagnosis and repair

- `tgw doctor check` is **read-only and runs as any actor**.
- Repairs are **root-only** through the single escalation:
  `sudo -n /usr/local/sbin/tgw-coding-bootstrap --commit <40hex> --repair <target>`,
  and only with canonical source clean.
- Declared repair targets: `context`, `context-launcher`, `runtime`,
  `database`, `unix-git-access`, `workers`, `plan-render-worker`,
  `obsolete-surfaces`.
- Known probe limitations in an ordinary session (a FAIL may be a false
  negative): `services.local-coding` (units are active but invisible to the
  session), `services.plan-render` (`/proc/<pid>/cwd` is root-only),
  `context.launcher` cold probe (needs sudo). `context.snapshot` FAIL is real
  and means the published context is stale relative to canonical source;
  repair it before trusting `tgw-context` MCP, then start a fresh harness
  session (`context.clients` RESTART_REQUIRED).

## 5. The `/tmp` pressure valve

`/tmp` is tmpfs: RAM-backed, `size=10G`, `nr_inodes=1M`, cleared on reboot.
This is a deliberate protection against messy sessions — an agent that fills
`/tmp` fills RAM, not disk; reboot clears it; disk inodes are never
exhausted. It is also a **different filesystem** than the btrfs worktrees, so
same-fs operations cannot use it.

Consequences:

- Never bind `/opt/TGW` (btrfs) over `/tmp` for agents: that would remove the
  size/inode bound and a messy agent would fill the real disk permanently.
- Never treat `/tmp`, `/var/tmp`, or `$HOME` as a durable or same-fs scratch.
- Agents needing scratch use their per-actor same-fs root under `/opt/TGW/var/
  tmp` (see 3.3); durable roots are the designed `/opt/TGW/w` and
  `/opt/TGW/var/cache/tgw`.

## 6. Reconciliation rules

- After any permission/ownership sweep, run `tgw doctor check` and confirm the
  lock, runtime, launcher, and unix-group checks PASS before starting work.
- A missing repair verb is a backlog item (add the declared repair to the
  bootstrap `--repair` choices); never hand-repair records or queues outside
  the Doctor's declared surface.
- After any chmod/chgrp of shared roots, verify the same-filesystem rule:
  scratch used by Git/lease/reflink paths must stay on the worktree device
  (btrfs `/opt/TGW`), and named-user ACLs must not appear on group-shared
  roots (`setfacl -b` them; plain group permissions only).
