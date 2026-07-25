# Uh-huh v2 — Low-cost capture with faithful interim intake formatting

**Status:** staged addendum to `UH-HUH-TOOL-PROPOSAL-2026-07-25.md`
**Reason:** Dave clarified the design goal: make the early listening/capture phase cheap, then spend primary reasoning on the complete picture.

## Three-stage design

### Stage 0 — Presence acknowledgement (near-zero cost)

On each continuation while Uh-huh is active, return a fixed minimal acknowledgement (`uh-huh`, `mm-hm`, `following`) without normal answer generation, tool routing, planning, or analysis. This is interaction control, not reasoning.

### Stage 1 — Faithful capture plus interim intake formatting (low cost)

Append each user turn to an ordered raw buffer. At release—or optionally at a user-visible checkpoint for a long monologue—run a low-cost, non-authoritative formatter over the raw turns.

The formatter produces an **intake sheet**, not a conclusion:

- ordered assertions/observations, retaining wording where precision matters;
- stated preferences, decisions, constraints, and examples;
- named systems/files/hosts/tasks;
- candidate questions, actions, risks, and dependencies;
- ambiguity, contradiction, and terms needing verification;
- a raw-turn reference for every entry.

It must not invent facts, choose priorities, make recommendations, call tools, mutate state, or discard the raw buffer. If uncertain, it writes `unclear` rather than completing the thought.

### Stage 2 — Primary reasoning (expensive / deliberate)

The primary reasoning model receives both the raw ordered transcript and the labeled intake sheet. Raw turns are authoritative if they conflict. It synthesizes the whole picture only after release, separating:

1. Dave’s stated content;
2. verified/source-backed facts;
3. uncertainty or required lookup;
4. agent analysis and proposals;
5. actions that need explicit approval.

## Cost and fidelity controls

- Stage 0 should avoid an LLM call where the surface/session implementation permits a fixed response.
- Stage 1 uses a cheaper formatter/classifier model or deterministic extraction where sufficient; it is advisory and never commits data.
- Stage 2 is reserved for the released whole thought and any necessary evidence-gathering/reasoning.
- Keep raw text until Dave explicitly releases, saves, or discards the capture. Never replace raw content with its formatted representation.
- Show the active mode and, after formatting, make the intake sheet inspectable/correctable before consequential tool work.

## Acceptance scenario

Dave explains a complex plan over many messages. Hermes acknowledges cheaply, retains the ordered raw thread, generates a correctable intake sheet on release, and spends higher-cost reasoning only on the complete thought. The final response does not confuse extracted candidate actions with Dave-approved instructions.
