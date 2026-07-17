# INPROGRESS — tgw-runner-review skill (todo #1355, PP-HERMES-EA-001)

**Started:** 2026-07-13, same session as the tgw-coder executor profile.

## What's happening
Building the counterpart to `.claude/agents/tgw-coder.md`: a **skill**
(not an agent persona) that any "executive monitor" — Tigwa, Claude, or
whoever gets chosen next — follows identically to review a completed task
branch + result manifest against the work-packet spec and `invariants.md`,
apply bounded fixes, and escalate only on an explicit loss-of-control
trigger. Dave's framing: "Tigwa" is convenient nomenclature for this
project's first real user of the role, not a hardcoded dependency — the
skill itself must be persona-agnostic.

## Where I am
Writing `.claude/skills/tgw-runner-review/SKILL.md`, mirroring the
contract already written into
`docs/TGW-Plan-Vault/plan/pp/PP-HERMES-EA-001.md` §"Tigwa as branch-review
enforcer": load only the packet + manifest + invariants (same context
discipline as tgw-packet/tgw-coder), check diff against spec, bounded fix
attempts (cap from that doc, currently 2, not yet Dave-confirmed),
explicit out-of-control trigger list (kept in sync with the plan doc, plan
doc is the source of truth on conflict), clean path writes a
`<id>-REVIEW.md` marking "cleared for stitch" without self-merging (stitch
remains a separate human/Claude action per the uberscripting-not-autonomy
framing — [[feedback-uberscripting-not-autonomy]]).

## Not yet decided / left open
- Exact escalation channel (Telegram via Hermes-lite? `notify()`? both?)
  not yet wired — skill will note where to escalate but not assume a
  specific mechanism is live.
- Fix-attempt cap value still just a proposal (2) pending Dave confirming.

## Recovery note
If interrupted: check whether `.claude/skills/tgw-runner-review/SKILL.md`
already exists before redoing; check todo #1355.
