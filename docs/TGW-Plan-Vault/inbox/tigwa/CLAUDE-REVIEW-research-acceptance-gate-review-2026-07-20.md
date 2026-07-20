# Review: Research acceptance gate review

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T18:00Z
**Todo:** #1594

Independent review of the research-acceptance-gate proposal (guided-Perplexity request + all-submissions addendum + Syncthing addendum), read against the evidence-fortification principle note. Design review only; nothing implemented or authorized by this reply.

## 1. Missing states / unsafe transitions / ambiguous terms

- The 5-state lifecycle has no explicit terminal "declined" state — only "rejection, deferral... must remain recoverable." An item that Dave looks at and says no to should land somewhere distinguishable from an item nobody has looked at yet, both logged with who/when/why. Otherwise "declined" and "not yet reviewed" collapse into the same bucket over time and the record loses the fact that a decision was actually made.
- No correction/supersession state is modeled, even though the console requirements (point 5 of the original request) name it. Given the fortification principle's "provenance-bearing... history, not a one-time pass" and this project's own append-only precedent (agent-trace evidence, invariant E14), an accepted artifact's category/role should never be edited in place. Model it as accepted(v1) -> superseded-by -> accepted(v2), both versions kept, not a mutated row.
- "capture-staged" is being used as a permanent resting state (the Syncthing addendum: arrival/sync never promotes it), not just a transient step. Worth saying explicitly: capture-staged has no TTL and is never garbage-collected — same permanence default as Prime Directive 1 (raw is permanent).
- A single guided session can produce more than one artifact (full transcript, an excerpt, an export). The proposal treats "a capture" as atomic but doesn't say whether a derived excerpt is a child of the full transcript. Recommend mapping this onto the project's existing raw/derived split (Data Charter, and the same split PP-AGENTTRACE-001 already used for transcripts vs. index/render): the full transcript or export is raw and permanent; an excerpt is derived and recomputable from it, and should record what it was derived from.

## 2. Does the gate prevent accidental canonization while preserving recovery?

At the policy level, yes. The real risk is the same one E14 was built to close: a policy that depends on every future code path remembering never to write into the canonical shelf without Dave's explicit action is exactly the failure class a written rule doesn't reliably survive. Recommend that whatever storage backs "operator-accepted" enforce the boundary mechanically — e.g. a canonical-index row can only exist with a non-null accepted_by/accepted_at, checked at write time, not left to application discipline. This is the same shape of fix as trace-immutability-guard.py, applied to a different write path.

Also: if capture-staged items are ever rendered alongside accepted ones in Obsidian or a future console, the rendering must visually distinguish the two — otherwise "discoverable" staged material can be mistaken for canon just by being visible in the same list.

## 3. Acceptance evidence and rendered-instance linkage

The provenance fields listed (source/export, retrieval time, citations, hash-where-bytes-retained) are solid. Two additions:

- Given the scope now includes agent-created source inventories and syntheses, the staged record should carry the originating agent_run_id where one exists — PP-AGENTTRACE-001 already built exactly this identifier (agent_runs table, #1580, merged). An agent-authored research submission should link back to the exact run that produced it, not just say "agent" in prose. This ties the two evidence systems together instead of building a second, parallel notion of "who produced this."
- The proposal doesn't yet give artifact_kind a closed vocabulary (session-transcript / export / link / citation-set / prompt-template / agent-synthesis / manual-capture). A reusable prompt/template is explicitly called out as not being acceptance of a later run — good — but it should still be a capturable artifact_kind in its own right (accepting a good prompt shape is a different act than accepting a specific run's output), and a closed enum lets later code branch safely instead of parsing free text.

## 4. Retention vs. acceptance vs. synthesis vs. plan input vs. implementation authorization

Well separated at the policy level. One gap: "plan input" / "decision input" appears only as a role tag chosen at acceptance time, not as its own lifecycle state, which is correct — but nothing yet says how a PP or master-plan entry that actually uses an accepted artifact should reference it. Recommend: citation by artifact ID in the PP/plan text, not re-derivation or copy-paste of the content — same "no orphan objects, everything findable/addressable" principle already standing in this project. Otherwise the link between "this PP decision" and "the evidence that justified it" only exists informally.

## 5. Future UI/console

The listed requirements (destination, provenance, consequence, rejection/deferral, correction/supersession) are the right set. Add:
- Show full history per artifact (staged -> accepted -> superseded chain), not just current state — required by the fortification principle's own "provenance-bearing change/anomaly history" standard, not just current status.
- Log a reason with any decline/defer, with actor + timestamp, same discipline as acceptance — an unexplained "no" is itself an anomaly worth preserving, per the fortification note.
- Since intake can now come from Claude, Tigwa, or Dave directly, consider filtering/staging by originating actor reusing the existing inbox/<actor>/ pattern already established for mailbox — a natural fit rather than inventing a new per-actor structure.

## 6. Interaction with PP-EVIDENCE-001

This whole design reads as a specific instance of PP-EVIDENCE-001's general problem (staged external evidence -> operator acceptance -> durable record), not a separate concern. Recommend it wait on, or explicitly plug into, whatever generic staged-evidence/commitment primitive PP-EVIDENCE-001's Stage 0 audit (todo #1589, in progress) lands on, rather than research-intake inventing its own one-off hash/provenance schema now. Concretely: if #1586 Leg A (PP-AGENTTRACE-001's insert-only sha256 hash-commitment table, reject-second-write-for-same-id) lands first, research-intake's acceptance record should reuse that same commitment shape rather than a bespoke one — both are "make an accepted evidence row immutable" problems with the same solution.

## Fortification-principle lens applied

Per the fortification note's own warning: this review should not be read as endorsing a hash alone, or a synchronized copy, or a single accept action, as sufficient durable assurance. The independent-witness technique PP-AGENTTRACE-001 landed on (Syncthing Send-Only on the producing side, Receive-Only + staggered versioning on a separate host with no write credentials back) is the right pattern to reuse here too, once a concrete design is authorized — not proposing it be built now.

## Net assessment

No unsafe transition found that would let staged material silently become canonical under the design as written. The gaps above are additions (declined state, versioning/supersession, artifact_kind enum, agent_run_id linkage, mechanical write-boundary enforcement, PP-EVIDENCE-001 primitive reuse), not corrections to something wrong. Still design-only — nothing here authorizes implementation.
