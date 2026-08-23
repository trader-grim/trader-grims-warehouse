---
name: tgw-review
description: Independently review an exact TGW source candidate, branch, commit, execution card, or remediation against its bound Plan intent, acceptance conditions, invariants, and evidence. Use for code review, candidate admission review, pre-merge review, remediation review, or when asked whether TGW work is safe to admit. The skill is provider-neutral and applies to any qualified Codex, Claude Code, Hermes, Aider, native, or future review harness.
---

# TGW review

Review one exact candidate. Treat review as an evidence-producing role, never as
authority to merge, install, deploy, approve, or repair the candidate.

## Establish session generation

Before independent or diagnostic review work on the first use in an ordinary harness
session, call the installed `tgw_context_status` tool with no arguments. Surface its
exact `generation_status.line` before retrieving review context. A `CURRENT` result
permits the role to proceed under its bound evidence. An `UPDATE_PENDING`,
`RESTART_REQUIRED`, `MIXED`, or `HOLD` result constrains review claims to the reported
generation and prevents an admitting verdict until exact current evidence is restored;
it never blocks, delays, or overrides an explicit owner command. If the status call is
unavailable or malformed, governed review is `HOLD`; never substitute launcher stderr,
conversation memory, or a filesystem guess.

## Classify the review

Choose one mode before inspecting code:

- **Governed independent review:** require an immutable independent-review
  execution card or Promptcraft handoff with exact Plan, solution, source,
  CodeGraph, environment, authority, acceptance, lease, and receipt-sink
  bindings. Missing or mismatched bindings are `HOLD`, not an invitation to
  reconstruct them.
- **Operator-requested diagnostic review:** review the named commit or diff and
  report findings, but label the result `NON_ADMITTING_DIAGNOSTIC`. Do not
  represent it as an admitted independent-review receipt.

Skill availability does not qualify a harness or admit its output. Provider
qualification, health, isolation, authority, and receipt validation remain
separate checks.

## Establish exact context

1. Call `tgw_context_bundle` with the actual review task. For governed review,
   supply the execution card's complete challenge, canonical card JSON, handoff
   hash, resource-receipt hash, skill-contract hash, and grant JSON; retrieve
   candidate and base only from the returned `registered_resources`.
2. Verify its approved Plan commit, source commit/tree, and CodeGraph freshness
   hash. Retrieve the cited Plan and runbook chunks needed for this review.
3. For a governed review, exact-compare the bundle with the execution card.
   Refuse stale cards, mutable source fallbacks, embedded Plan copies, chat
   reconstruction, or an absent CodeGraph represented as present.
4. Establish independence from implementation: use a separately admitted
   execution identity and clean context with no unrecorded private reasoning or
   mutable implementation work state. Same-vendor review is allowed only when
   the execution identities and contexts are independent.
5. Inspect only the exact candidate and base furnished by the execution card and its
   admitted inspection tool or source binding. Never discover or substitute a local
   checkout, assume that the base is named `main`, or infer that a Todo/PP completion
   implies parent Plan completion.

## Review the candidate

Review against the card's cited specification, exclusions, acceptance, and
relevant invariants. Check at least:

- every required behavior exists and every exclusion remains untouched;
- correctness, boundary conditions, failure and ambiguity handling, cleanup,
  concurrency, and recovery behavior;
- authority/effect boundaries, secret handling, path and process trust,
  persistence atomicity, replay/freshness, and evidence integrity where
  relevant;
- tests and static checks named by the bound environment, including whether they
  executed the candidate bytes rather than another editable install or mutable
  checkout;
- source, test, review, admission, deployment, live verification, and operator
  acceptance as separate states; and
- deploy or migration implications as observations only, never actions.

Do not substitute a broad unbound `pytest` or lint run for card acceptance.
Additional read-only diagnostics are allowed when they remain within the card's
authority and are reported as additional evidence.

## Produce the verdict

Lead with findings ordered by severity. Each finding must identify the exact
candidate-relative path, line when meaningful, consequence, and reproducing
evidence. Do not hide a real finding in a summary.

For a governed machine review, emit exactly the contract required by the
launcher. The current semantic runner expects `tgw-code-review/v1`:

```json
{
  "schema": "tgw-code-review/v1",
  "verdict": "PASS",
  "snapshot_hash": "sha256:<bound snapshot>",
  "summary": "Concise evidence-based conclusion",
  "findings": []
}
```

Use `FAIL` with one or more exact findings when any issue remains. Use `PASS`
only when all required evidence is present, every acceptance condition was
checked, and no unresolved finding remains. Write only to the bound receipt
sink when the launcher provides one.

For a diagnostic review, return findings, checks run, unknowns, and the explicit
`NON_ADMITTING_DIAGNOSTIC` label. Never mint an admission receipt yourself.

## Handle remediation

Do not edit the candidate during independent review. If the operator or a new
execution card separately authorizes remediation, stop the review, create a
successor candidate in an isolated worktree, and require a fresh independent
review of the successor. Never amend or silently repair a reviewed candidate.

Never merge, publish, install, deploy, change Plan authority, mutate production,
or mark a Plan/PP/Todo complete from this skill.

## Recovered contracts

Read [references/recovered-contracts.md](references/recovered-contracts.md)
when reconciling an old `tgw-pr-review` or `tgw-runner-review` invocation or
explaining why an old review artifact is not current admission evidence.
