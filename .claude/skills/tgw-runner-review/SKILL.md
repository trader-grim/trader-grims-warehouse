---
name: tgw-runner-review
description: Check a completed task branch + result manifest against its work-packet spec and invariants, apply bounded fixes, escalate only on loss-of-control. This is the "runner contract" for whichever executive monitor is reviewing a tgw-coder (or equivalent) task branch — Tigwa, Claude, or any future reviewer follows the same steps. Use when the user says /tgw-runner-review <todo-id>, or hands over a task branch for review before stitching.
---

# TGW Runner Review

Review exactly ONE task branch, produced by the branch-per-task contract
(the bound approved Plan materialization's `plan/pp/PP-HERMES-EA-001.md` §"Tigwa as branch-review
enforcer"). This skill is written generically on purpose: "Tigwa" is this
project's convenient name for whichever agent currently fills the reviewer
role, not a hardcoded dependency. Any executive monitor invoking this skill
follows the same steps and produces the same artifacts.

**Read the plan doc's framing before running this**: as of 2026-07-13 this
whole contract is Dave's own code-review/git process made intelligent —
"uberscripting," not autonomous execution. This skill's clean-path output
hands off to a human/Claude stitch step; it does not merge to main itself.

## Usage

> /tgw-runner-review <todo-id>

## Inputs — load ONLY these, same context discipline as tgw-packet/tgw-coder

- The bound approved Plan materialization's `plan/packets/<id>-*.md` — the packet's Spec,
  Out-of-scope, and Acceptance sections.
- The task's separate execution/evidence store — the executor's result
  manifest (status, files touched, live evidence, deviations, out-of-scope
  findings filed). The approved Plan materialization is read-only.
- The `todo/<id>-<slug>` branch diff — read via `git diff main todo/<id>-<slug>`
  or `git show todo/<id>-<slug>:<path>` from the shared repo checkout; no
  need to `cd` into the executor's worktree (`/opt/TGW/var/worktrees/<id>-<slug>`,
  mandatory as of 2026-07-13 — see PP-HERMES-EA-001.md) since branch refs
  are shared across worktrees in the same repo.
- `docs/TGW-Plan-Vault/reference/invariants.md` — only the invariants
  relevant to the files actually touched, not the whole document.

Do not load the master plan, FUTURE-IDEAS, or unrelated packets.

## Steps

### 1. Manifest sanity check — before judging anything else

If the result manifest is missing, or missing any of {status, files
touched, live evidence}: this is itself an out-of-control condition (see
step 3) — **escalate immediately**, do not attempt to reconstruct what the
executor should have written.

**If the work was done in a worktree** (mandatory since 2026-07-13): the
`tgw` venv's editable install is pinned to the shared checkout, so a test
run against the worktree that didn't override `PYTHONPATH` may have
silently validated the wrong copy of the code. If the manifest's live
evidence doesn't show `PYTHONPATH` being overridden to the worktree path
(or an equivalent confirmation of which copy was tested), treat that
evidence as unverified, not as a pass — ask for it to be redone with the
override, don't wave it through.

### 2. Check the diff against the packet

- Every item in the packet's **Spec** is actually implemented — no more,
  no less. Cadence/TTL/limit/default values match exactly what's stated;
  anything the executor chose that wasn't in the spec must appear in the
  manifest's "Deviations" field, not be silently present in the diff.
- Every file touched is inside the packet's declared scope. Anything in
  the **Out of scope** list must NOT appear in the diff.
- The manifest's "Live evidence" is an actual observed result (URL, log
  line, item JSON diff, fresh API read) — not "tests pass" alone, and not
  a description of what *should* happen.
- Relevant `invariants.md` entries for the touched paths are not violated
  by the diff.

### 3. Out-of-control trigger list — explicit, not subjective

This list is authoritative in
the bound approved Plan materialization's `plan/pp/PP-HERMES-EA-001.md` — if the two ever
disagree, that doc wins and this skill needs updating, not the reverse.

- Spec deviation not resolved within the fix-attempt cap (below).
- An `invariants.md` violation still present after fix attempts.
- Any file touched outside the packet's declared scope — **except** test
  file(s), new or modified, for a function/module already in the packet's
  declared scope. Writing tests is part of the process, not scope creep —
  the carve-out is about WHAT is tested, not whether the file already
  existed. Tests for anything else, or new test frameworks/fixtures/
  conftest changes unrelated to the fix, still fire this trigger.
- Any attempted live/production write before the stitch step.
- Todo/pp_ref mismatch, or a packet with no explicit Spec section at all.
- The manifest sanity check in step 1 failed.

If any of these fire and are not resolvable within the cap below:
**escalate** (step 5) and stop. Do not merge, do not mark the todo done.

### 4. Bounded fix attempts — capped, not open-ended

- Cap: **2 fix attempts** (per PP-HERMES-EA-001, proposed default — not
  yet confirmed by Dave; treat as authoritative until the plan doc says
  otherwise).
- A fix attempt means: make the minimal change on the same branch to bring
  the diff back into conformance with the packet's Spec/Out-of-scope/
  invariants, then re-run the packet's Acceptance step to get fresh live
  evidence. Update the result manifest's Deviations field with what was
  fixed and why.
- After 2 attempts, if the branch still fails step 2/3: escalate
  regardless of how close it looks to clean. The cap exists so no
  reviewer's judgment is the only thing standing between a drifting fix
  and Dave.

### 5. Escalate

- Write `<id>-ESCALATION.md` to the task's separate execution/evidence store:
  which trigger fired, what was tried, current branch/diff state, and the
  specific decision needed from Dave.
- Surface it through whatever channel is actually live for this reviewer
  (Telegram via Hermes-lite, a direct message, `notify()`) — this skill
  does not assume a specific mechanism is wired; state that a channel
  choice is a deployment detail, not part of the review logic.
- Stop. Do not attempt further fixes, do not merge, do not mark the todo
  done.

### 6. Clean path — hand off to stitch, don't self-merge

If steps 2–4 pass with no unresolved trigger:

- Write `<id>-REVIEW.md` to the task's separate execution/evidence store:
  `status: cleared`, reviewer identity, which packet/spec sections were
  checked, and a one-line summary.
- This is a **silent pass-through** — normal case, Dave does not need to
  see this file to know it happened (per the "escalation-only reporting"
  rule). It is discoverable (no orphan objects) but not paged.
- Do **not** merge the branch, rebase onto main, or mark the todo `--done`
  — the actual stitch action is a separate, explicit step performed by
  Dave or a Claude session, per PP-HERMES-EA-001's "stitch step
  unchanged." This skill's job ends at "cleared for stitch."
- **Required pre-merge step, whoever performs the stitch (todo #1366,
  PP-HERMES-EA-001):** this REVIEW.md write got silently skipped for 6 of
  7 concurrent-batch-stitched todos in one session (#1280/#1282/#1284/
  #1288/#1291/#1297), only caught and backfilled after the fact. Before
  actually merging/stitching any batch, run the mechanical gate —
  `python3 scripts/check_review_md.py <id> [<id2> ...]` (or
  `--scan-branches` to check every local `todo/<id>-<slug>` branch at
  once) — and do not stitch any id it reports missing. This does not
  replace this skill's own step 6 write; it's the check that catches the
  write having silently not happened.

## Constraints

- One branch per invocation.
- Never touch anything outside the packet's declared scope while fixing —
  a fix that requires touching out-of-scope files is itself an escalation
  (step 3), not something to route around.
- Never bypass the `tgw-api` fence, never alter eBay OAuth scopes, secrets
  stay in `secrets_root` — same standing constraints as every other worker.
- Cost-awareness (token/API spend for this review) is a soft signal worth
  noting in the manifest, never a hard gate that substitutes for the
  explicit trigger list above.
- **Any operational friction hit during review or stitch — a permission
  mismatch, a tooling quirk, a stale environment assumption — gets a todo
  filed, always, not just when the reviewer happens to remember.** Same
  standing as flagging a spec deviation: mandatory, not optional. A
  narrow, reversible workaround to keep going is fine; it doesn't replace
  the todo.
