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
   - effective uid `0` (root — receipt-driven recovery/bootstrap) or a
     configured publisher identity (`db` by default) → **allowed**;
   - `TGW_MAIN_REF_GUARD_OVERRIDE='<reason>'` set → **allowed**, and the use is
     appended to `<git-common-dir>/tgw-main-ref-guard/override-events.log` as a
     durable JSON line;
   - anything else → **refused**, the ref does not change, and the hook prints
     an actionable message.
3. If the guard module cannot be imported it fails closed (refuses the `main`
   update).

The hook is the only artifact. It is fully reversible: remove the file (or run
`uninstall`) to restore the previous behaviour exactly. The durable override log
is intentionally kept across an uninstall.

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

| state | meaning |
| ----- | ------- |
| PASS  | hook present, tgw-managed, executable, matches its recorded config |
| WARN  | hook absent, or present but its recorded config is missing |
| FAIL  | integrity problem — foreign hook in the slot, not executable, or modified after install |
| UNKNOWN | status could not be read |

## Relationship notes

- `#1942` is **not** superseded by `#1965` (deploy gate). The gate stops
  non-`main` refs from deploying; this guard stops non-publisher refs from
  becoming `main`. They are complementary.
- `src/tgw/protected_git.py` stays orthogonal: that is deterministic read-only
  Git for service accounts (a read guard), not a ref guard.
