# Work packet — #1663 fix check_review_md.py --scan-branches worktree prefix bug

**Todo:** #1663
**Plan:** PP-HERMES-EA-001
**Executor:** `tgw-coder`
**Base:** live-verified `catio-nix-0.0.1-alpha`
**Context budget:** This packet, `scripts/check_review_md.py`, and any existing tests for it (`tests/test_check_review_md.py` if present — otherwise a new test file scoped to this script only). Do not load the master plan or unrelated packets.

## Objective

Fix `scripts/check_review_md.py`'s `_discover_branch_ids()` so `--scan-branches` includes
branches currently checked out in another git worktree, not just the one (if any) checked out
in the primary/shared checkout.

## Reproduced baseline (live, confirmed 2026-07-28)

```text
$ git branch --list 'todo/*'
  todo/1597-multimodel-defaults
  todo/1598-multimodel-hardcoded-sweep
+ todo/1604-multi-order-sold-fix
+ todo/1607-dedupe-key-audit
... (13 more '+'-prefixed lines)

$ python3 scripts/check_review_md.py --scan-branches
OK   #1597: ...
OK   #1598: ...
CLEAR: all 2 todo(s) have a -REVIEW.md, safe to stitch.
```

15 of 17 local `todo/<id>-<slug>` branches are silently absent from the scan. `git branch
--list` prefixes a branch checked out in the *current* worktree with `* ` and a branch checked
out in *any other* worktree (which is the normal case here — mandatory `tgw-coder` worktree
isolation per PP-HERMES-EA-001 means nearly every task branch lives in its own worktree) with
`+ `. `_discover_branch_ids()`'s `line.strip().lstrip('* ')` only strips characters in the set
`{'*', ' '}` from the left — `+` is not in that set, so a `+`-prefixed line never has its
prefix stripped, the branch name never matches the `^todo/(\d+...)-` regex, and the id is
silently dropped from the scan with no warning. This defeats the exact purpose of the
`--scan-branches` mode (todo #1366's "catch a missing -REVIEW.md before stitch, mechanically,
not by memory") for the majority case it exists to cover.

## Spec

1. Edit only `scripts/check_review_md.py` (plus a new or existing test file for it) — no other
   application code.
2. Fix `_discover_branch_ids()` to correctly recognize a branch line regardless of its `git
   branch --list` prefix: no prefix, `* ` (current worktree), or `+ ` (checked out in another
   worktree). Do not assume only these three prefixes are possible — parse defensively (e.g.
   strip the single leading marker-and-space pattern `git branch --list` documents, rather than
   a fixed character set).
3. Do not change the script's other behavior: `check_ids()`, `find_review_md()`, the CLI
   surface (`--scan-branches`, positional `todo_ids`), and exit codes must be unchanged.
4. Add a regression test that reproduces the bug against synthetic `git branch --list` output
   (or, if simpler and still deterministic/offline, against a real throwaway git repo with a
   worktree-checked-out branch) proving: (a) a `+`-prefixed branch's id IS now discovered, and
   (b) a `*`-prefixed and a plain (no-prefix) branch line still work as before. Do not rely on
   the live repo's actual current branch/worktree state for the test — it must be reproducible
   standalone.
5. Do not touch any actual git worktree or branch in the shared checkout or in
   `/opt/TGW/var/worktrees/` — this is a pure script/test fix, no git mutation of any kind.

## TDD sequence

1. Add the regression test(s) from spec point 4 against the *current* (buggy) implementation.
   Run and observe the expected failure (the `+`-prefixed case fails; the `*`/plain cases may
   already pass — that's fine, keep them as non-regression coverage).
2. Implement the minimal fix to `_discover_branch_ids()`.
3. Re-run; all new and existing tests pass.
4. As a live (read-only) sanity check — not part of the automated test — you may run
   `python3 scripts/check_review_md.py --scan-branches` from your worktree copy of the script
   against the real repo's branches to confirm it now reports more than 2 ids. This is a
   read-only invocation (the script only reads `git branch --list` and checks file existence);
   do not let it influence anything beyond your own observation in the result manifest.

## Worktree

Create a fresh isolated worktree and branch:

```text
/opt/TGW/var/worktrees/1663-scan-branches-worktree-prefix
todo/1663-scan-branches-worktree-prefix
```

If either already exists, stop and report the collision rather than reusing or deleting it.

After creating the worktree, copy this packet byte-for-byte from the canonical shared path
`/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/plan/packets/1663-scan-branches-worktree-prefix.md`
into the worktree at the same repository-relative path, and include it unchanged in the branch
commit.

## Acceptance

```text
tgw-pytest /opt/TGW/var/worktrees/1663-scan-branches-worktree-prefix <new/existing test file for check_review_md.py> -q
tgw-pytest /opt/TGW/var/worktrees/1663-scan-branches-worktree-prefix -q
```

Also run Ruff on every changed file using the canonical project environment. Record exact
commands and outputs. A green focused test without the required RED evidence is insufficient.

## Deliverable

Commit only the bounded branch changes and write:

```text
docs/TGW-Plan-Vault/plan/packets/results/1663-RESULT.md
```

Include RED and GREEN evidence, files touched, the exact parsing fix, focused/full-suite/Ruff
outcomes, deviations, and out-of-scope findings.

## Authority and stop conditions

- No shared-checkout edits.
- No merge, rebase, push, deploy, service action, queue action, Todo closure, eBay/API
  mutation, credential action, backup/sync action, flake edit, or canonical Plan Vault
  acceptance.
- No mutation of any real git branch/worktree outside your own task worktree/branch.
- Do not touch #1697, #1705, or #1706's worktrees/branches.
- Stop if the packet is inconsistent with the live bug reproduction above, the worktree/branch
  already exists, or acceptance requires production access.
