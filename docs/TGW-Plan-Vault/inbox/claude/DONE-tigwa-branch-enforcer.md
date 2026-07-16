# INPROGRESS — Tigwa branch-review enforcer structure (todo #1354, PP-HERMES-EA-001)

**Started:** 2026-07-13, live conversation with Dave (not a scheduled packet).

## What's happening
Dave wants a worker-agnostic task-execution contract (branch-per-task +
result manifest, same shape discussed for the coming streamlined
Aider-like executor profile) plus a Tigwa check/fix review loop that sits
between worker output and Dave: Tigwa checks branch output against the
work-packet spec + invariants, does bounded fixes, and reports to Dave
**only on loss-of-control** — not routine approval.

## Key decision already made this session
Dave explicitly chose (AskUserQuestion, 2026-07-13): this is a
**deliberate bounded exception** to PP-HERMES-EA-001's settled sequencing
("autonomy unlocks only once the crypto-lock exists / cage comes last") —
not a redefinition that it doesn't count as autonomy, not a wait-for-the-
cage deferral. Scoped narrowly (her own task-review loop, branch-isolated,
never touches live/production directly), explicit rather than silent.

## Where I am
About to write a new section into
`docs/TGW-Plan-Vault/plan/pp/PP-HERMES-EA-001.md` under the Authority
model, covering: the worker-agnostic branch+manifest contract, Tigwa's
bounded check/fix loop anchored to spec/invariants (not general taste), an
explicit encoded "out of control" trigger list (not her subjective call),
and the stitch/merge step. Will cross-link PP-AIOPS-001's still-open
"litterbox autonomy level" question as answered by this, for the code-
review case specifically (not the data-mutation litterbox, which is
separate).

## Not yet decided / left open in the doc
- The actual "out of control" detector list — needs its own pass once the
  mechanism is being built, not fully specified in this planning session.
- Whether Tigwa's MCP write scope needs to change to support this (it's
  currently read-only, `TGW_MCP_READONLY=1`) — flagged, not resolved here.
- Fix-attempt cap value (proposed 2, not confirmed by Dave).

## Recovery note
If interrupted: check whether PP-HERMES-EA-001.md already has the new
section before redoing this; check todo #1354 status.
