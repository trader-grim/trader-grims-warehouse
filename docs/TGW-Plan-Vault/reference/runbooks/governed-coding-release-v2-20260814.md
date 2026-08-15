# TGW governed coding and release workflow — v2 (2026-08-14)

**Owner:** shared; operator authority: Dave

**Last verified:** 2026-08-14 through implementation, complete tests, immutable
candidate commits, source generation selection, wheel activation, service restart,
and authenticated live UI verification

**Applies to:** TGW application/Nix work governed by `tgw-plan/v2`

**Last drill:** commits `50d2751a8` and `afb8e7037`, 4,031 tests passed with
5 expected skips, deployed and verified on the exact affected item page

## Outcome this workflow is designed to produce

A completed coding request is not “an agent wrote code.” It is a chain of separately
bound facts:

```text
approved intent and complete solution
  → bounded execution card/task
  → isolated implementation
  → tests and static verification
  → independent review when required
  → closed commit/tree candidate
  → immutable source selection
  → matching runtime activation
  → affected service restart
  → live operator acceptance
  → durable receipts and rollback identity
```

A failure at any arrow creates a new candidate or explicit hold. Never mutate an
already reviewed candidate and continue calling it the same candidate.

## Approval and interruption rule

Approval of the exact Plan commit and complete solution authorizes every build,
review, integration, immutable candidate installation, and other effect declared by
that solution. Do not pause between these phases to ask for repetitive approval.

Ask the operator only when:

- the requested effect is outside or broader than the solved Plan/task;
- a destructive or external/provider effect was not declared;
- recovered intent conflicts materially with the request;
- a missing choice would change the product behavior, target, or blast radius.

Progress messages do not replace execution. “Continue” means continue through the
remaining already-authorized phases, not stop at the next receipt.

## Stage 1 — bind the standalone Plan

Use the shared `tgw-plan` skill and only `/opt/TGW/library/plans` as Plan authority.
Read the complete Plan v2 specification, selected Plan/PP/Todo, execution graph,
solution, and relevant reconciliation evidence before changing source.

```bash
PLAN_ROOT=/opt/TGW/library/plans
APPROVED_PLAN=f0a8cf22b2c7b2f064292a048ffcb8ee98919e99
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=safe.directory \
GIT_CONFIG_VALUE_0="$PLAN_ROOT" \
python3 /home/codex/.codex/skills/tgw-plan/scripts/verify_plan_root.py \
  "$PLAN_ROOT" "$APPROVED_PLAN"
```

Record the approved ref independently of Plan-repository evidence HEAD. The installed
platform solution is bound to Plan commit `fb9fee3...`, solution `d28650...`, and
closure `bc0c53...`; do not silently rewrite those identities to the later approved
proposal ref.

Default to the complete Plan root. Use a PP or Todo only when the operator explicitly
narrows the work. Completion of a Todo does not imply completion of its parent.

`CLAUDE.md` is Claude Code's contract, not a generic repository contract. Codex,
Hermes, Tigwa, Aider, and other harnesses follow their own instructions plus the
standalone Plan and exact task/card.

## Stage 2 — reconcile installed and stranded work

Before claiming a function is absent, inspect:

- all local and registered Git worktrees and branches;
- dirty/untracked files and ownership;
- reflogs/unreachable commits when relevant;
- immutable releases and selection receipts;
- installed venv module paths/hashes;
- Nix flake/unit/runtime bindings;
- tests, review receipts, and successful live run evidence.

Compare behavior and content, not branch names or dates. Preserve user/other-agent
changes. Do not use `git reset --hard`, broad checkout, or unreviewed cleanup.

For production bugs, reproduce the exact live state before editing. In the
2026-08-14 item-action incident, a unit test modeled `result` at the top level while
PostgreSQL persisted it under `payload_json.result`; authenticated live verification
caught the mismatch. Production-shaped fixtures are required at every serialization
boundary.

## Stage 3 — establish a clean candidate worktree

Use an isolated worktree from the exact admitted parent. Record:

```bash
git status --short
git rev-parse HEAD^{commit} HEAD^{tree}
git branch --show-current
```

Do not develop directly in `/home/db/tgw-flake` for application changes. Do not let a
test runner write source/evidence into the candidate checkout. If a worktree is dirty,
attribute every path before proceeding.

Use `apply_patch` or another reviewable edit mechanism for source changes. Keep the
diff minimal and avoid mixing documentation, product, deployment, and unrelated
cleanup unless the Plan explicitly couples them.

## Stage 4 — implement with production-shaped evidence

For every changed behavior:

1. add a regression that fails on the prior source;
2. model the actual database/HTTP/filesystem/process shape;
3. retain unrelated behavior with a negative/control test;
4. handle ambiguity and retries according to the durable contract;
5. verify user-visible action/state, not only an internal helper;
6. avoid turning diagnosis or observation into a provider/business effect.

For UI actions, test the exact action row, `onclick`/request action, and absence of
unsafe Retry controls. For workers, bind queue envelope, generation, graph, condition,
lease, result, and receipt. For Nix, validate the rendered unit/tool identities.

## Stage 5 — run the test gates in a closed test environment

This repository's reusable test interpreter is currently:

```text
/opt/TGW/tgw-lib/src/trader-grims-warehouse/.venv/bin/python
```

An isolated worktree is not itself installed into that venv. Explicitly point Python
at the candidate source or tests may silently import a different checkout:

```bash
TEST_ROOT=$(mktemp -d /opt/TGW/w/4/tgw-test.XXXXXX)
chmod 700 "$TEST_ROOT"
mkdir -m 700 "$TEST_ROOT/tmp" "$TEST_ROOT/log"

export PYTHONPATH=<CANDIDATE-WORKTREE>/src
export TMPDIR="$TEST_ROOT/tmp"
export TGW_LOG_ROOT="$TEST_ROOT/log"
```

Do not use shared `/tmp` when it is full or when trusted-path tests require private
ancestors. `TGW_LOG_ROOT` prevents false permission failures from tests trying to
write production `/opt/TGW/var/log`.

Run in increasing scope:

```bash
# Exact regression first
<PYTHON> -m pytest -q <EXACT-TEST-NODE>

# Affected subsystem
<PYTHON> -m pytest -q <AFFECTED-TEST-FILES>

# Static gates
<RUFF> check <CHANGED-PYTHON-FILES>
<PYTHON> -m py_compile <CHANGED-PYTHON-FILES>
git diff --check

# Full tracked suite
<PYTHON> -m pytest -q
```

Record exact pass/fail/skip counts and environment. An environment-only failure is
not source failure, but it must be explained and rerun correctly; never erase or
relabel it. The final candidate must have a clean, correctly configured run.

## Stage 6 — independent review and controller verification

Risky or production-facing candidates require independent review of the exact commit
and tree. Independence means a separate admitted execution identity/context without
unrecorded private implementation state; it is not permanently tied to a vendor name.

Review must examine:

- governing specification and exclusions;
- complete diff and call paths;
- serialization/identity/trust boundaries;
- retry, ambiguity, cleanup, and rollback behavior;
- production-shaped adversarial tests;
- evidence truthfulness;
- whether live acceptance actually proves the requested result.

A blocker creates a new commit and review. A review result never authorizes an effect
outside the Plan. Controller verification independently checks the candidate/evidence
closure; implementation self-tests cannot substitute for it.

## Stage 7 — close the candidate

Commit only the intended files:

```bash
git diff --check
git status --short
git add -- <EXACT-FILES>
git diff --cached --check
git diff --cached
git commit -m '<MESSAGE>'
git rev-parse HEAD^{commit} HEAD^{tree}
git status --short
```

Never use `git add -A` or `git add .` in a worktree containing unrelated changes.
The commit/tree become the candidate identity. Any later change gets a successor
commit.

## Stage 8 — build exact source and wheel artifacts

### Source archive

```bash
BUILD_ROOT=$(mktemp -d /opt/TGW/w/4/tgw-build.XXXXXX)
chmod 700 "$BUILD_ROOT"
git archive --format=tar -o "$BUILD_ROOT/source.tar" HEAD
git get-tar-commit-id < "$BUILD_ROOT/source.tar"
sha256sum "$BUILD_ROOT/source.tar"
tar -tf "$BUILD_ROOT/source.tar" | sed -n '1,20p'
```

The archive must be unprefixed. Check that `src/` and `pyproject.toml` are at its
root. Verify the embedded commit and exact Git tree before transferring.

### Offline wheel

Build from the same commit without fetching dependencies. The 2026-08-14 accepted
pattern used a private build virtualenv, the already bundled setuptools wheel, and:

```bash
<BUILD-PYTHON> -m pip wheel \
  --no-deps --no-build-isolation \
  --wheel-dir "$BUILD_ROOT/dist" .
```

Verify:

```bash
sha256sum "$BUILD_ROOT"/dist/*.whl
unzip -p "$BUILD_ROOT"/dist/*.whl tgw/<CHANGED_MODULE>.py | sha256sum
sha256sum src/tgw/<CHANGED_MODULE>.py
```

Use a valid wheel filename. The filename parser rejects arbitrary shortened names
even when the bytes are a valid wheel.

## Stage 9 — install and activate

Follow `full-platform-installation-v2-20260814.md`.

1. transfer artifacts through the approved maintenance path;
2. verify hashes on `tgw-prod`;
3. dispatch registered `app-release-install/v1` with exact expected-current CAS;
4. verify the selected immutable generation;
5. install the matching wheel in the production venv under the separately approved
   runtime activation boundary;
6. restart only affected services, with guaranteed restart on failure;
7. verify installed module hashes and live process start times.

Do not declare deployment after step 4. The running services use the venv.

## Stage 10 — live acceptance

Acceptance is specific to the request:

- HTTP/UI: authenticated ordinary navigation, exact rendered control/data, no stale
  action, no new error logs;
- worker: one bounded current-generation canary, exact receipt, no duplicate dispatch;
- coding: request through role execution, independent review/controller, candidate,
  release, and live receipt;
- Nix: current closure, affected unit contracts, health, rollback readiness;
- provider effect: only the exact separately authorized effect, plus reconciliation.

Never use installation authority to click an eBay publish/list action. For the
2026-08-14 UI repair the acceptance verifier proved `List on eBay` was present but
did not invoke it.

If live verification contradicts tests, treat the candidate as failed. Diagnose the
production shape, add a regression, create a successor commit, rerun tests/review,
and install the successor. Do not patch production without bringing the fix back into
the closed source lineage.

## Stage 11 — handoff and cleanup

Report:

- user-visible outcome first;
- exact commit/tree/release/wheel/module identities;
- tests/review/controller results;
- selected generation and receipt;
- affected services and live verification;
- no-effect boundaries (for example, listing button not clicked);
- warnings/gaps still open;
- rollback identities.

Remove only named temporary transfer/build/test artifacts after verifying they are no
longer needed. Prefer recoverable trash; if permissions force deletion, use explicit
task-specific paths and report that the temporary artifacts are unrecoverable but
reproducible from the commit.

## Installed coding surface and current limitation

The supported operator CLI is:

```bash
tgw coding start --todo-id <ID> \
  --object-generation <GENERATION> \
  --source-commit <40-HEX-COMMIT>
tgw coding status <REQUEST-ID>
tgw coding log <REQUEST-ID>
tgw coding stop <REQUEST-ID>
tgw coding access-status [<REQUEST-ID>]
```

The standalone `tgw-coding` script is not supported in the current generation; its
entry point imports a missing `coding_cli.main`. Use the `tgw coding` subcommand.

At last live verification, `tgw coding access-status` reported the host but returned
unknown provider/role/receipt status, and `tgw-lib` retained failed historical
`tgw-coding-provision@*.service` units. Therefore do not call automated coding
provision operational until a fresh bounded request completes all role lanes and
returns a durable receipt. Manual coding under an exact Plan/task remains available
and must follow this same candidate/review/install/live-acceptance workflow.

## Failure classifications

| Failure | Required response |
|---|---|
| Wrong source imported during tests | Fix `PYTHONPATH`, rerun; do not change source to satisfy a wrong checkout |
| `/opt/TGW/var/log` permission errors in tests | Set private `TGW_LOG_ROOT`, rerun |
| `/tmp` full/path too long | Use private short task root on `/opt/TGW/w/4`; preserve failed attempt |
| Review blocker | New commit and review |
| Release expected-current mismatch | Hold and reconcile current selection; never force |
| Wheel install fails after service stop | Guaranteed restart, retain prior runtime, diagnose before retry |
| Live behavior fails despite tests | New production-shaped regression and successor candidate |
| Provider/role access unknown | Hold automated dispatch; do not fabricate provider status |
| Ambiguous provider effect | Preserve evidence and reconcile read-only; never resend |

## Completion rule

“Complete” means the requested function meets its specification in the live installed
system and has a rollback/evidence chain. It does not mean an agent finished editing,
tests passed, a commit exists, a release was selected, or a service is active.
