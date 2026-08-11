# Work packet — #1538 controller-owned post-subagent verification gate

**Todo:** #1538
**Plan:** PP-AGENT-DISCIPLINE-001
**Executor:** `tgw-coder`
**Base:** live-verified `catio-nix-0.0.1-alpha`
**Context budget:** This packet, `scripts/check_review_md.py` and
`scripts/scan_out_of_process_edits.py` (style/structure reference only — same "deterministic,
git-based, receipt-writing tool" shape), and the two prior result manifests
`docs/TGW-Plan-Vault/plan/packets/results/1663-RESULT.md` /
`docs/TGW-Plan-Vault/plan/packets/results/1602-RESULT.md` (for the established environment
facts: `tgw-pytest` doesn't exist on this system, use
`PYTHONPATH=<worktree>/src pytest`; `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH` needed for
`libz.so.1`). Do not load the master plan or unrelated packets.

## Background — mechanism decision already made, do not relitigate

Right now "`pytest -q` passes, Ruff is clean" is a claimed acceptance criterion in every
work-packet's result manifest — nothing actually re-runs and verifies it mechanically before a
task branch is handed to the reviewer role. A `PreToolUse` hook cannot fix this:
`docs/TGW-Plan-Vault/reference/invariants.md` E11 (updated 2026-07-20, todo #1531) confirms
`anthropics/claude-code#69260` — `PreToolUse` hooks never fire for `Agent`-tool-spawned
subagents at all, and `tgw-coder` always runs as a subagent. Prose in the coder's own
instructions is not enforcement either (that's exactly the trust-model gap E11 exists to name).

**Decided mechanism (Tigwa review, 2026-07-27,
`docs/TGW-Plan-Vault/inbox/claude/TIGWA-DIRECTIVE-2026-07-27-1538-controller-gate.md`):** a
**controller-owned, post-subagent, pre-review/pre-stitch verification gate** — not a hook, not
prose. After a coder subagent returns and its worktree is frozen, the controller (whoever is
running the sprint — Claude, Tigwa, or a future actor) runs one deterministic,
repository-owned script against the exact todo/worktree/packet/branch. It reruns the focused
tests and Ruff, checks diff/worktree scope, and writes a durable, committed receipt. A missing
or failing receipt fails closed — no PASS, no cleared-for-stitch state, no stitch. This is
preventive at the promotion boundary (nothing gets to "cleared for stitch" without a receipt),
even though it runs after file edits rather than blocking them — the only viable shape given
the confirmed subagent-hook limitation.

**This packet builds the verifier tool only.** It does NOT change `tgw-runner-review`'s own
process (the separately-attributed reviewer still reviews the actual diff and reruns its own
required independent checks per the existing skill — the verifier's receipt is an input to
that process, not a replacement for it). It does NOT wire anything into a hook or CI. It does
NOT touch `.claude/skills/tgw-runner-review/SKILL.md` (that file lives outside
`src/tgw/`/`tests/`, so it is out of scope for a `tgw-coder`-executed E12 packet — a future,
separate change can point that skill at this tool once it exists and is proven).

## Objective

Build `scripts/verify_task_branch.py` — a deterministic, repository-owned verifier the
controller runs against one already-committed task branch/worktree before treating it as
reviewable. It reruns the packet-declared focused tests and Ruff, checks that the branch's
changed paths stay within an explicit scope allowlist, and writes a durable, machine-readable
receipt recording exact commands, environment, revisions, exit codes, and pass/fail per check.

## Spec

1. New standalone script: `scripts/verify_task_branch.py`. Do not modify
   `check_review_md.py`, `scan_out_of_process_edits.py`, or any hook/skill file.
2. CLI:
   ```
   python3 scripts/verify_task_branch.py <todo-id> \
       --worktree <path> \
       --base-ref <ref> \
       [--test <pytest-path-or-nodeid> ...] \
       [--scope-prefix <repo-relative-prefix> ...]
   ```
   - `<todo-id>`: required, the numeric todo id being verified.
   - `--worktree`: required, absolute path to the already-created, already-committed task
     worktree (this tool never creates or mutates a worktree/branch).
   - `--base-ref`: required, the git ref the task branch is compared against for scope-checking
     (e.g. `catio-nix-0.0.1-alpha`).
   - `--test`: zero or more pytest path/nodeid arguments to run as the "focused tests" for this
     task. If none given, the tool must still run (scope-check + Ruff-on-changed-files only) and
     say explicitly in its receipt that no focused test command was provided — never silently
     treat "no tests given" as "tests passed."
   - `--scope-prefix`: zero or more additional repo-relative path prefixes allowed in the diff,
     beyond the standing default-allowed process-artifact set (see point 4).
3. **Test/Ruff execution:** run every `--test` argument via
   `PYTHONPATH=<worktree>/src pytest <args> -q` inside the worktree (matching the established
   `tgw-pytest`-doesn't-exist environment fact from #1663/#1602 — do not hardcode a nonexistent
   `tgw-pytest` binary). Run `ruff check` against every `*.py` file the branch actually changed
   (derived from the diff, not a fixed list). Capture exit code, stdout/stderr (or a truncated
   but representative excerpt — do not silently drop failure detail), and wall time for each.
4. **Scope check:** compute `git diff --name-only <base-ref>..<branch-tip-in-worktree>` (branch
   tip = worktree's current `HEAD`). Every changed path must match one of:
   - a path under any `--scope-prefix` given, OR
   - the standing default-allowed process-artifact set: `docs/TGW-Plan-Vault/plan/packets/*`
     (the packet copy), `docs/TGW-Plan-Vault/plan/packets/results/*` (RESULT.md/REVIEW.md/
     VERIFY.md), `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-*` (the breadcrumb).
   Any path matching neither is a scope violation — record it explicitly in the receipt as a
   failure, do not silently pass.
5. **`git diff --check`:** run and record pass/fail (whitespace-conflict-marker check), same
   as every prior packet's acceptance step.
6. **Receipt:** write `docs/TGW-Plan-Vault/plan/packets/results/<id>-VERIFY.md` — a
   machine-readable-enough (Markdown with clearly labeled sections, not prose the next tool has
   to guess at) receipt containing: todo id, worktree path, base ref, candidate commit hash,
   exact commands run (verbatim, copy-pasteable), environment variables set, exit code per
   check, a truncated output excerpt per failing check, the scope-check result (list of changed
   paths + which allowlist rule matched each, or FAIL for any that matched none), and one
   overall `VERIFY: PASS` or `VERIFY: FAIL` line as the first line of the file (so a future
   caller can grep it without parsing prose). This receipt is written INTO THE WORKTREE (same
   convention as every `-RESULT.md`/`-REVIEW.md` so far) — the verifier itself does not commit
   it; that is the controller's job after invoking this tool (matches how `-REVIEW.md` has been
   handled in every prior packet this sprint).
7. **Exit code:** `0` if every check passed (all tests passed, Ruff clean, scope clean, `git
   diff --check` clean), non-zero otherwise. Unlike `scan_out_of_process_edits.py` (#1602, pure
   reporting), this tool is a real gate — its exit code is meant to be checked by the caller and
   is the "fails closed" mechanism the directive requires.
8. **Never mutate git state** — no `git commit`, `checkout`, `merge`, `reset`, `clean`,
   `stash`. Read-only `git diff`/`git log`/`git show` plus running `pytest`/`ruff` as
   subprocesses (which read/execute code but do not alter the worktree's git state) only.
9. **Never mutate the shared/integration checkout** — always operate against the `--worktree`
   path given, never `/opt/TGW/src/trader-grims-warehouse` directly (guard against this
   explicitly — refuse and error out if `--worktree` resolves to the shared checkout path).

## TDD sequence

1. Build a throwaway synthetic worktree fixture (temp dir, `git init`, a couple of commits,
   a trivial passing test file and a trivial failing test file) — never the real TGW repo.
   Write regression tests FIRST against the not-yet-built script, observe RED (script doesn't
   exist / functions don't exist), then implement.
2. Cover, at minimum: (a) all tests pass + Ruff clean + scope clean → `VERIFY: PASS`, exit 0;
   (b) a focused test fails → `VERIFY: FAIL`, exit non-zero, receipt shows which test and an
   output excerpt; (c) Ruff finds an issue → `VERIFY: FAIL`; (d) a changed path outside every
   allowlist rule → `VERIFY: FAIL`, scope section names the offending path; (e) `--test`
   omitted entirely → receipt explicitly says no focused test command was provided, does NOT
   claim tests passed; (f) `--worktree` pointed at something resembling the shared checkout
   path → tool refuses with a clear error, does not run.
3. As a live (read-only, safe) sanity check — not part of the automated test — you may run this
   tool against one of this sprint's own already-cleared worktrees (e.g.
   `/opt/TGW/var/worktrees/1663-scan-branches-worktree-prefix`, `--base-ref
   catio-nix-0.0.1-alpha`, `--test tests/test_check_review_md.py`) to confirm it reproduces
   `VERIFY: PASS` against a branch already known-good. This does not write anything outside
   that worktree and does not require touching #1697/#1705/#1706/#1663/#1602's own committed
   history — it only reads.

## Worktree

```text
/opt/TGW/var/worktrees/1538-controller-verify-gate
todo/1538-controller-verify-gate
```

If either already exists, stop and report the collision rather than reusing or deleting it.

Copy this packet byte-for-byte into the worktree at the same repo-relative path and include it
unchanged in the branch commit.

## Acceptance

```text
tgw-pytest /opt/TGW/var/worktrees/1538-controller-verify-gate tests/test_verify_task_branch.py -q
tgw-pytest /opt/TGW/var/worktrees/1538-controller-verify-gate -q
```

(`tgw-pytest` does not exist on this system — use the equivalent `PYTHONPATH=<worktree>/src
pytest` invocation, same as #1663/#1602. Not a new deviation.)

Run Ruff on every changed/new file. Record exact commands and outputs.

## Deliverable

```text
docs/TGW-Plan-Vault/plan/packets/results/1538-RESULT.md
```

Include RED/GREEN evidence, the synthetic-fixture test design, the live read-only sanity-check
output against a real already-cleared worktree, focused/full-suite/Ruff outcomes, deviations,
and an explicit note that skill-file wiring (`tgw-runner-review`) and hook/CI integration are
both deliberately out of scope for this packet.

## Authority and stop conditions

- No shared-checkout edits (and the tool itself must refuse to run against the shared checkout
  — spec point 9).
- No merge, rebase, push, deploy, service action, queue action, Todo closure, eBay/API
  mutation, credential action, backup/sync action, flake edit, or canonical Plan Vault
  acceptance.
- No modification of `.claude/skills/`, `.claude/hooks/`, or `.claude/settings.json` — those
  are out of scope for this packet regardless of how tempting wiring them in seems.
- No mutation of any real git branch/worktree outside your own task worktree/branch. If you run
  the live sanity check against another sprint worktree, it must be read-only (this tool does
  not commit anything on its own — spec point 6 already establishes that).
- Do not touch #1697, #1705, #1706, #1663, or #1602's worktrees/branches (read-only sanity
  check against one of them, per TDD point 3, is fine — do not write to it).
- Stop if the packet is inconsistent with the live E11 wording, the worktree/branch already
  exists, or acceptance requires production/DB/credential access.
