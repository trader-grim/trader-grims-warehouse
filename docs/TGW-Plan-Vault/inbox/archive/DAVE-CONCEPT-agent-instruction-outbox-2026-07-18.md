# DAVE CONCEPT — Agent instruction outbox / prompt-improvement interface

**From:** Dave, captured and structured by Tigwa
**To:** Claude
**Date:** 2026-07-18
**Status:** Product/design discussion input only — not implementation authorization.
**Relationship:** Evolves the earlier informal “Justshoutit” idea into a durable human-to-agent workbench.

## Problem being solved

Dave needs a place to capture a stream of thoughts, requests, corrections, and instructions intended for agents without interrupting the current task or accidentally dispatching them. He wants Tigwa to improve/structure the communication before it is sent to Claude or another capable agent, while retaining Dave’s original intent and control.

This is not primarily a generic prompt editor and is not a normal todo list. It is a personal, agent-aware instruction outbox: a queue of unsent, reviewable units of communication that can be delivered at an appropriate moment.

## Core interaction

```text
Dave types/speaks rough intent
→ an unsent instruction card is captured
→ Tigwa proposes a clearer, target-appropriate version (possibly alternatives)
→ Dave chooses, edits, or says “try again”
→ prompt checker identifies missing/conflicting details
→ Dave explicitly sends it to the selected agent, or leaves it stacked/deferred
→ resulting response/artifact/outcome is linked back to the card
```

A card must never be automatically sent merely because it is captured or improved.

## Product principles

1. **Raw intent is preserved.** The original Dave input remains visible and immutable as the source record. Improved drafts are proposed renderings, never silent replacements.
2. **Explicit delivery only.** No agent receives the card until Dave takes an affirmative send action.
3. **Stackable deferred thought capture.** Dave can enter multiple unsent cards during active work and dispatch them later when context and timing are appropriate.
4. **Target-aware, not Claude-only.** Initial targets are Tigwa and Claude; the model should extend to future similarly capable agents without redesign.
5. **Helpful checker, not bureaucracy.** The checker advises by default. It must not invent requirements, silently broaden authority, or turn rough thought into a heavyweight ticket.
6. **Durable communication record.** Sent cards retain the target-rendered message, delivery destination, timestamp, relevant artifact/hash where applicable, and outcome/follow-up linkage.
7. **Separate communication from task execution.** An instruction card may later produce a todo, Claude inbox request, Tigwa investigation, or discussion; none should be automatic.

## Suggested instruction-card model

```text
Raw input
Prepared draft(s)
Target agent
Intent type
Delivery boundary
Context links
Checker findings
Delivery record
Outcome / response link
```

Recommended field meaning:

| Field | Meaning |
|---|---|
| Raw input | Dave’s exact captured wording. |
| Prepared drafts | Tigwa-generated alternatives or revisions, each attributable/versioned. |
| Target agent | Tigwa, Claude, or another future supported agent. |
| Intent type | Question, request, decision, correction, review, research, etc. |
| Delivery boundary | Discussion-only, inspect, propose, implement, or side-effecting; never inferred silently. |
| Context links | PP, todo, artifact, item/SKU, source, or earlier instruction card. |
| Checker findings | Clear warnings/questions plus suggested repair language. |
| Delivery record | What was actually rendered/sent, when, to where, and verified durable path/hash if relevant. |
| Outcome link | Reply, artifact, resulting decision, or a follow-up instruction card. |

## State model

```text
captured
→ drafting
→ ready
→ deferred / queued-for-later
→ sent
→ acknowledged
→ resolved / archived
```

States must describe the communication lifecycle, not claim that the requested work is complete. “Sent” is not “done.”

## Prompt checker examples

The checker should make uncertainty visible and offer a repair, for example:

```text
⚠ No acceptance outcome named
→ Suggest: “Ask Claude to return a current-state triage table with citations.”

⚠ Implementation requested but delivery boundary is absent
→ Ask/offer: design-only, branch-only, or production-authorized?

⚠ A referenced issue/source is ambiguous
→ Suggest linking the relevant PP, triage artifact, task, or source.

⚠ Possible conflict with an existing Dave decision
→ Surface the decision and its date; do not block or override Dave.
```

The checker is an advisor, not an autonomous policy engine. Its behavior should be legible: “Here is what I think you mean; here are unresolved choices; here is a target-ready draft.”

## Delivery modes

1. **Send now** — deliver the approved target-specific rendering through the durable channel/inbox.
2. **Hold until context** — retain it unsent, optionally tagged to a milestone such as after a named triage, task, review, or agent response.

Potentially useful later: a card can be related to a milestone but must not auto-send merely because a model infers that the milestone occurred. Any automation needs an explicit delivery rule and visible confirmation.

## What makes this different from a todo list

```text
Todo:
  Work that needs doing.

Instruction card:
  A prepared unit of human-to-agent communication,
  awaiting explicit delivery or further refinement.
```

A card may eventually create or reference a todo, but it should not be forced into task tracking just to preserve a thought.

## Initial design question for Claude

Evaluate this as a product/domain design, not an implementation request. In particular, propose:

1. the smallest durable data model that preserves raw input, revisions, target renderings, explicit-send control, and outcome linkage;
2. how it should coexist with the existing Plan Vault mailbox and taskboard without becoming a second canonical tracker;
3. a minimal first UI/workflow that proves value before scheduling, autonomous dispatch, multi-agent orchestration, or broad automation;
4. safety/integrity boundaries for advice versus actual delivery; and
5. open design choices that Dave should decide before a PP/build packet is created.

Do not implement, modify a tracker, create a service, add automatic sending, or alter agent authority from this concept note.