# Review: 1621 flakegate
Status: cleared
Reviewer: Claude (main session, tgw-runner-review)
Todo: #1621   PP: PP-FLAKEGATE-001

## Checked
- Manifest sanity (step 1): status/files-touched/live-evidence all present;
  PYTHONPATH/LD_LIBRARY_PATH override confirmed pointing at the worktree's
  code before live testing.
- Diff vs. base (`catio-nix-0.0.1-alpha`, not `main` — branch cut correctly):
  8 files, all within declared scope (`src/tgw/flake_gate.py` new,
  `src/tgw/queue/state_machine.py`, `src/tgw/api.py`, `tests/test_flake_gate.py`
  new, `.claude/agents/nix-flake-maintainer.md`, `invariants.md`, inbox
  breadcrumb, result manifest). No out-of-scope files touched.
- Spec conformance: matches the dispatched packet exactly — `flake_gate.py`
  never shells out to git/nixos-rebuild for push or switch (only `audit`'s
  read-only `git fetch`/`git log`); `mark_flake_mutation_executed()` has a
  proper rowcount guard against a silent no-op update (same shape as
  invariant C14); `nix-flake-maintainer.md` Step 2/5 rewritten to request
  instead of execute, with an explicit standing prohibition on ever calling
  `mark-executed` itself.
- Invariant E17 write-up (docs/TGW-Plan-Vault/reference/invariants.md):
  accurate, matches implementation.
- No premature live/production write: all live-evidence test rows were
  inserted into and then deleted from the real `state_machine` Postgres DB
  (confirmed via the manifest's cleanup query showing count=0 after).

## Deviations (both resolved, no escalation trigger)
1. Master-plan write-up (authored before dispatch) described the closing
   command as executing the real push/switch; the dispatched packet said
   record-only. Executor correctly followed the packet (more detailed, and
   the safer design — no callable code path ever executes the mutation).
   Reconciled: `TGW-Master-Plan.md`'s PP-FLAKEGATE-001 section corrected in
   this review pass to match what was built.
2. `tgw flake audit`'s live test ran against a disposable throwaway repo,
   not `~/tgw-flake` itself, due to a `tgw`/`db` OS-user permission split
   (Postgres peer auth vs. filesystem read access). Legitimately out of
   this packet's scope to fix unilaterally — filed as todo #1623, noted in
   invariant E17 as a known gap. Mechanism itself (git log parsing + DB
   comparison + rollout-date filtering) is fully live-verified.

## Out-of-scope findings filed (not this packet's problem, correctly not fixed here)
- #1622 — pre-existing `test_invariant_c12_field_set_accessors.py` failure,
  confirmed present on base branch via `git stash` before this packet's
  changes.
- #1623 — `tgw flake audit --repo ~/tgw-flake` OS-user permission split.

## Process note (not a trigger, but worth naming)
No formal packet file exists at `docs/TGW-Plan-Vault/plan/packets/1621-*.md`
— this was dispatched as an inline Agent prompt with a complete Spec/
Out-of-scope/Acceptance section, same as the already-tracked #1617 gap
(todo #1615/#1618 had the same pattern). The dispatch prompt itself was
detailed enough to review against; this is a filing-hygiene gap, not a
missing-spec problem.

## Summary
Clean build, correctly and more conservatively scoped than my own earlier
master-plan draft. No trigger fired. Cleared for stitch.
