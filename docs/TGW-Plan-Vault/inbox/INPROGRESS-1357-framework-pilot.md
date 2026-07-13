# In progress — todo #1357: pilot the tgw-coder/tgw-runner-review framework

**PP:** PP-HERMES-EA-001
**Started:** 2026-07-13

## Plan (Dave, 2026-07-13)
Rough test protocol: set up → run one task, evaluate → run a small batch,
check again → fix/repeat until a batch is done → stitch powwow (review
session, merge together) rather than merging as each clears.

## First pilot task
Packet written: `docs/TGW-Plan-Vault/plan/packets/1292-1293-clipd-rofi-picker.md`
— covers todos #1292+#1293 together (deliberately, flagged: same function,
causally stacked bugs, no live-acceptance possible fixing only one). Small,
self-contained, no data/eBay risk — good first mechanics test.

## First stitch cycle — complete, 2026-07-13
Ran 5 tasks one at a time (#1292/#1293, #1295, #1290, #1294, #1289), all
under PP-COHESION-001. Two real trigger-list gaps found and fixed (both
about the test-file carve-out — see PP-HERMES-EA-001.md's out-of-control
list history), one structural fix (mandatory git-worktree-per-task
isolation, `/opt/TGW/var/worktrees/`), one framework hazard closed
(PYTHONPATH override required when testing worktree code, since the venv's
editable install points at the shared checkout). 5th run passed clean
with zero new findings. All 5 merged into `catio-nix-0.0.1-alpha`, full
suite green (2086 passed, 1 skipped) post-merge, todos closed, worktrees
and branches cleaned up.

## Cadence rule added, then proven (2026-07-13)
Dave set the standing rule: stitch immediately after each single task
clears, EXCEPT the first task of a fresh sequence/risk-category is never
stitched alone — needs a second clean run (2-in-a-row) before stitching
either, at which point that sequence graduates to running several tasks
concurrently. Encoded in `PP-HERMES-EA-001.md`'s "Cadence rule" section.

## Shared-root cluster rule added, then proven on a real case
When multiple todos trace to the same underlying function, fix the root
alone first, then run a VERIFICATION PASS per dependent before writing
any new code — don't assume branch count up front. Proven on
#1274 (root: `config.py`'s unhardened `sku_dir()`/`location_dir()`) with
three dependents: #1273 collapsed to zero-code verification-only closure,
#1275 and #1284 turned out to be genuinely independent bypasses needing
their own fixes. Encoded in `PP-HERMES-EA-001.md`'s "Shared-root cluster
rule" section.

## Two real framework mistakes caught and fixed this session
1. **Test-file scope carve-out was too narrow** (found in tasks 1 and 4)
   — fixed by dropping "existing" from the trigger-list wording (tests
   for what you touched, new-or-existing, are always in scope).
2. **Claude's own prompts said "branch off `main`"** — wrong; this repo's
   actual active branch is `catio-nix-0.0.1-alpha` (`main` is a real ref
   but 41 commits behind, a stale ancestor). No harm resulted (caught
   before a real conflict), but `tgw-coder.md` now requires live
   verification of the base branch (`git branch --show-current`) instead
   of trusting any hardcoded name, including in the invoking prompt.

## New standing rule: operational friction always gets a todo
Any workaround for something that isn't the actual bug being fixed (a
permission mismatch, a tooling quirk) now requires a todo, not just an
ad hoc fix-and-move-on. Encoded in `PP-HERMES-EA-001.md`, `tgw-coder.md`,
and `tgw-runner-review/SKILL.md`.

## Where I am
Two full stitch cycles done — 5 tasks (mechanical `PP-COHESION-001` bugs),
then 5 more (SECURITY-tagged findings, including a 4-item shared-root
cluster). **14 real bugs fixed and merged into `catio-nix-0.0.1-alpha`**,
full suite green throughout (last confirmed: 2111 passed, 1 skipped).
Todo #1357 stays open — the pilot continues. Remaining untouched SECURITY
findings: `#1276`, `#1277`, `#1278`, `#1279`, `#1281`, `#1283`.

Also this session: reconciled two Tigwa inbox requests (Hermes-native
checkpoint adapter #1356, plan-review publishing folder #1359, and a
proposed `tgw-inbox-intake` skill #1362) — all approved as proposed, no
changes needed. She's operating well within her IN TRAINING scope.

## Recovery note
If interrupted: `git log --oneline -20` on `catio-nix-0.0.1-alpha` shows
all 10 merge commits (2 stitch cycles × mix of sequential/concurrent
merges). `docs/TGW-Plan-Vault/plan/packets/results/` has every task's
RESULT.md + REVIEW.md. Check todo #1357 and `PP-COHESION-001`'s remaining
SECURITY items for what's next. `docs/TGW-Plan-Vault/plan/pp/PP-HERMES-EA-001.md`
has the full contract (task-execution contract, cadence rule, shared-root
rule, trigger list, operational-friction rule) — read that before
resuming, not just this note.
