# Work packet — #1706 C12 semantic allowlist

**Todo:** #1706
**Plan:** PP-LISTEDITOR-001 / invariant C12
**Executor:** `tgw-coder`
**Base:** live-verified `catio-nix-0.0.1-alpha`
**Context budget:** This packet, `CLAUDE.md` invariant C12 lines, and `tests/test_invariant_c12_field_set_accessors.py`. Read source files only to verify and name the already-allowlisted expressions. Do not load the full master plan or unrelated packets.

## Objective

Replace the C12 detector's raw `(path, line_number)` allowlist with stable semantic anchors so unrelated line insertions do not fail the suite, while any new, removed, changed, or duplicated direct `item_attributes` / `item_specifics` access still requires explicit review.

## Reproduced baseline

From `/opt/TGW/src/trader-grims-warehouse`:

```text
nix develop path:/home/db/tgw-flake -c env PYTHONPATH=src python -m pytest tests/test_invariant_c12_field_set_accessors.py -q

2 failed, 1 passed
```

The failures are symmetric line drift:

- `http_server.py`: reviewed hits moved `3014→3021`, `3073→3080`, and `6048→6055`.
- `workers/ai_identify.py`: reviewed hits moved `276→316`, `336→376`, and `431→471`.
- No new behavior is established by that output; verify each expression against its existing allowlist justification before preserving it.

## Spec

1. Edit only `tests/test_invariant_c12_field_set_accessors.py`, unless a test-first result proves a second test-support file is strictly necessary. Do not change application code merely to satisfy the detector.
2. Replace line-number identity with a Python semantic identity derived from parsed source, not raw position. Each hit must identify at least:
   - repository-relative path;
   - enclosing module/class/function scope;
   - access kind (`get` or subscript);
   - key (`item_attributes` or `item_specifics`);
   - a normalized representation of the containing access/expression sufficient to distinguish the reviewed use.
3. Preserve multiplicity. Use a multiset/count-aware comparison or equivalent so copying an otherwise identical allowed access within the same scope is detected rather than collapsed by a set.
4. Keep an explicit reviewed allowlist with a one-line reason for every sanctioned hit. The allowlist must not silently accept all occurrences in a file or function.
5. Continue excluding only the two sanctioned accessor modules and the existing migration script exactly as today.
6. The detector must fail closed for:
   - a new unauthorized direct access;
   - removal or semantic change of an allowlisted access;
   - an extra duplicate of an allowlisted semantic access;
   - malformed Python in a scanned file, rather than silently skipping it.
7. Ordinary comments, blank lines, imports, or unrelated statements inserted before a reviewed hit must not change its identity.
8. Keep failure output actionable: relative file, enclosing scope, access kind/key, and enough normalized source/AST context to find and review the hit. Do not expose absolute worktree paths in the allowlist.
9. Do not weaken invariant C12, broaden the exemptions, or merely refresh line numbers.

## TDD sequence

Work in vertical RED→GREEN cycles and retain the evidence in the result manifest:

1. Add a regression test proving that inserting unrelated lines before an allowed access leaves its semantic identity unchanged. Run it and observe the expected failure under the current line-number implementation.
2. Implement the minimal semantic scanner/identity needed to make that test pass.
3. Add and satisfy separate tests for unauthorized new access, stale/changed allowed access, and duplicate multiplicity.
4. Run the detector against the real repository and reconcile every existing hit to its current documented justification. Do not auto-approve a changed expression.

## Worktree

Create a fresh isolated worktree and branch:

```text
/opt/TGW/var/worktrees/1706-c12-semantic-allowlist
todo/1706-c12-semantic-allowlist
```

If either already exists, stop and report the collision rather than reusing or deleting it.

After creating the worktree, copy this packet byte-for-byte from the canonical shared path
`/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/plan/packets/1706-c12-semantic-allowlist.md`
into the worktree at the same repository-relative path. Include that unchanged packet in the branch commit so the reviewable branch remains self-contained; do not edit the shared copy.

## Acceptance

Use the canonical environment and verify imports resolve from the worktree:

```text
tgw-pytest /opt/TGW/var/worktrees/1706-c12-semantic-allowlist tests/test_invariant_c12_field_set_accessors.py -q
tgw-pytest /opt/TGW/var/worktrees/1706-c12-semantic-allowlist -q
```

Also run Ruff on the changed test file using the canonical project environment. Record exact commands and outputs. A green focused test without the required RED evidence is insufficient.

## Deliverable

Commit only the bounded branch changes and write:

```text
docs/TGW-Plan-Vault/plan/packets/results/1706-RESULT.md
```

The result must include RED and GREEN evidence, files touched, semantic identity shape, reconciled allowlist count, focused/full-suite/Ruff outcomes, deviations, and out-of-scope findings.

## Authority and stop conditions

- No shared-checkout edits.
- No merge, rebase, push, deploy, service action, queue action, Todo closure, eBay/API mutation, credential action, backup/sync action, flake edit, or canonical Plan Vault acceptance.
- Do not touch #1697 or #1705 worktrees.
- Stop if the packet is inconsistent with current invariant C12, a reviewed hit's semantics changed, the worktree/branch exists, or acceptance requires production access.
