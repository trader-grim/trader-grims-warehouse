# Canonical `main` ref guard (Todo 1942)

`plan/reconciliation/DRIFT-PREVENTION-1942-MAIN-GUARD-20260901.md`

## Why

On 2026-08-31 a coding agent advanced `refs/heads/main` of the canonical
repository with a raw local `git merge` (no review, no release path). The raw
merge desynchronized the task-cursor commit, the runtime selector, and the
Context MCP snapshot, and recovery needed a `git reset --hard` plus a
receipt-driven runtime rollback.

`main` is protected: ordinary `tgw-coders` agents cannot advance it by raw Git.
The canonical HEAD and the task cursor advance together only through the
sanctioned source publisher — the `db`-owned coding-lifecycle integration path
(`tgw.development.local_workflow` foreman, `coding-git:fast-forward/v1`).

## Mechanism

`src/tgw/main_ref_guard.py` installs a Git `reference-transaction` hook on the
canonical repository. During the `prepared` phase the hook:

1. Pre-filters on stdlib only. If no line changes `refs/heads/main`, it exits 0
   without importing `tgw` (worktree branches, tags, notes, stash,
   remote-tracking refs, `HEAD`, `ORIG_HEAD` are never affected).
2. Otherwise it imports `tgw.main_ref_guard` and evaluates the caller:
   - a sanctioned publisher identity (`db` — the `DEFAULT_PUBLISHER_IDENTITIES`
     package constant, embedded literally into the hook body as
     `_PUBLISHER_IDENTITIES`, **not** read from any file) → **allowed**, no
     record;
   - `TGW_MAIN_REF_GUARD_OVERRIDE='<reason>'` set → **allowed**, and the use is
     appended to `<git-common-dir>/tgw-main-ref-guard/override-events.log` as a
     durable JSON line;
   - effective uid `0` (root — receipt-driven recovery/bootstrap) → **allowed**,
     but never silently: it produces the same durable override record (with
     `"implicit_root": true`) that an explicit override would;
   - anything else → **refused**, the ref does not change, and the hook prints
     an actionable message.

   The authorization list travels *inside* the tamper-anchored hook body. It is
   never loaded from the group-writable state directory, so an ordinary
   `tgw-coders` agent cannot widen it by editing `guard.json`; editing the
   embedded `_PUBLISHER_IDENTITIES` instead changes the hook body, which
   `guard_status` then reports as `modified` (FAIL).
3. If the guard module cannot be imported it fails closed (refuses the `main`
   update).

The hook is the only artifact. It is fully reversible: remove the file (or run
`uninstall`) to restore the previous Git behaviour exactly. The guard's state
directory (`<git-common-dir>/tgw-main-ref-guard/`, holding `guard.json` and the
durable override log) is intentionally kept across an uninstall — it is what
lets the Doctor tell a repo that was never guarded (`absent`) apart from one
whose hook was removed after installation (`removed`, escalated to FAIL).

The hook embeds the absolute git-common-dir that `install_guard` computed, so it
resolves the override log correctly even if `core.hooksPath` relocates the hooks
directory. `guard_status` re-derives the one body it will accept as genuine from
**trusted inputs only** — the installed package's own source directory
(`PACKAGE_SOURCE_PATH`), the common git dir resolved fresh from `git`, the
package constant `DEFAULT_PUBLISHER_IDENTITIES`, and the standard interpreter —
and compares byte-for-byte. Nothing is read back out of the installed hook or
`guard.json`, so a redirected `source_path`, a rewritten embedded allow-list, or
any body edit all read as `modified` (FAIL), even if `guard.json` is deleted or
its recorded hash forged in the same step. The tamper anchor is the installed
`tgw` package — for both the hook body *and* the authorization list.

## Installation / lifecycle

Coding-runtime provisioning of the canonical host is expected to run the
`install` command below. Until it has, `tgw doctor` reports
`source.main-ref-guard` as **WARN** and the overall run is **ATTENTION**
(exit 1) — this transient state is expected on a freshly provisioned host and
is not a tamper signal. Once installed, any later disappearance of the hook is
**FAIL**.

## Operator procedure

Install / update on the canonical repo:

```
python3 -m tgw.main_ref_guard install \
    --repo /opt/TGW/tgw-lib/src/trader-grims-warehouse
```

Verify:

```
python3 -m tgw.main_ref_guard status --repo /opt/TGW/tgw-lib/src/trader-grims-warehouse
tgw doctor        # source.main-ref-guard → PASS (green) when active
```

Emergency override (recorded):

```
TGW_MAIN_REF_GUARD_OVERRIDE='incident <id>: <reason>' git -C <repo> merge ...
```

Roll back:

```
python3 -m tgw.main_ref_guard uninstall --repo /opt/TGW/tgw-lib/src/trader-grims-warehouse
```

## Doctor

`tgw doctor` runs `check_main_ref_guard` (`source.main-ref-guard`), read-only and
provider-transferable:

| state | integrity | meaning |
| ----- | --------- | ------- |
| PASS  | `ok`, and `override_event_count == 0` | hook present, tgw-managed, executable, byte-for-byte the trusted package render, `guard.json` hash agrees |
| WARN  | `ok`, and `override_event_count > 0` | guard intact, but `refs/heads/main` was advanced out of band ≥ 1× (explicit override or recorded root advance) — review each event and rotate the log to clear |
| WARN  | `absent` | guard was never installed on this repo (expected on a freshly provisioned host) |
| WARN  | `config-missing` | hook body verified against the package but `guard.json` is gone |
| FAIL  | `removed` | guard was installed and the hook has since disappeared — possible tampering |
| FAIL  | `modified` / `foreign` / `not-executable` | a hook occupies the slot but is not the guard this package installs (includes a redirected `source_path`, a widened embedded allow-list, or a forged `guard.json` hash) |
| UNKNOWN | — | status could not be read |

## Residual risk / threat model

This is a `reference-transaction` hook in a `.git` directory that is
group-writable by `tgw-coders` (the population the guard targets). It is
**defense-in-depth plus tamper-evidence, not a hard boundary**:

- It stops the accidental / low-effort case — a plain `git merge`, `git push`,
  `git commit`, or `git update-ref` on `main` (the exact 2026-08-31 shape).
- A determined agent in `tgw-coders` can still bypass it in one step:
  `rm .git/hooks/reference-transaction`, `git -c core.hooksPath=/dev/null merge`,
  or a direct write to `.git/refs/heads/main` / `.git/packed-refs`. None of
  these are interceptable by any hook.
- Bypass by hook removal is **tamper-evident**: after installation the Doctor
  reports `removed` → FAIL (the state directory survives), so the canonical
  host's health turns red rather than silently amber. Editing the hook body —
  including redirecting its `source_path` or widening its embedded
  `_PUBLISHER_IDENTITIES` — is likewise FAIL (`modified`), anchored on the
  installed package, not on the group-writable `guard.json`.
- The `TGW_MAIN_REF_GUARD_OVERRIDE` env var is a deliberately low-friction
  emergency lever: it is *not* an authenticated identity and any caller can set
  it. It is not a silent bypass, though — every use (and every root advance) is
  appended to the durable, `fsync`-ed override log, and the Doctor drops the
  canonical host from PASS to **WARN** for as long as any such event is
  unreviewed. The stronger identity gate is the publisher-owned-ref arrangement
  below.

The strictly stronger control is a **publisher-owned-ref arrangement** — `main`
advanced only in a location the `tgw-coders` group cannot write (a separate
publisher-owned repo/remote, or `.git` ownership hardening so `.git/refs`,
`.git/hooks`, and `.git/packed-refs` are not group-writable). That is a host
provisioning / permissions change outside this bounded code leaf; it is the
recommended follow-up and is the option the plan leaves open
(`DRIFT-PREVENTION-1942`, §3: "update/pre-receive hook **or** publisher-owned
ref arrangement").

## Relationship notes

- `#1942` is **not** superseded by `#1965` (deploy gate). The gate stops
  non-`main` refs from deploying; this guard stops non-publisher refs from
  becoming `main`. They are complementary.
- `src/tgw/protected_git.py` stays orthogonal: that is deterministic read-only
  Git for service accounts (a read guard), not a ref guard.
