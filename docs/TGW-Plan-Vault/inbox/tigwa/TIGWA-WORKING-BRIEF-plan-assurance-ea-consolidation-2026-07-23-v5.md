# Tigwa working brief — plan assurance, EA consolidation, and execution support

**Date:** 2026-07-23
**Status:** Dave-directed working brief for tomorrow. Staged context, not a canonical Plan Vault amendment and not implementation authorization.

## Purpose

Prepare a durable process for turning Dave’s natural-language intent, research, corrections, and decisions into grounded machine-readable planning and execution communications—without making Hermes, Hermaroid, Claude Code, a provider, or an external agent harness a permanent authority.

## Settled direction

### 1. Durable versus replaceable

The system must preserve its useful work if any current tool or host is replaced.

```text
Durable:
  Dave’s decisions and intent
  canonical source/provenance and revision history
  machine-readable packets and authority contracts
  PostgreSQL work-state transitions
  evidence, tests, invariants, review, and supersession links

Replaceable adapters/resources:
  Tigwa/Hermes/Hermaroid
  Claude Code, Aider, OpenCode, Manus, and other agent harnesses
  model/provider/account/subscription
  host, laptop, desktop app, framework, graph/analytics implementation
```

Use outside tools as bounded execution substrates where they prove useful; do not make their private context, state, or roadmap the TGW system.

### 2. Tigwa’s intended role

Tigwa/Hermes/Hermaroid are temporary executive-assistant/librarian aids, not enduring monitoring, workflow, queue, or generic-execution infrastructure.

```text
Tigwa EA/librarian:
  - helps Dave research and problem-solve;
  - preserves and grounds evidence;
  - converts normal conversation into machine-readable communications;
  - models/digests plans, research, risks, contradictions, and decisions;
  - prepares packets and decision-ready options;
  - coordinates bounded requests, response follow-through, and review;
  - helps verify that claimed results have evidence.

Not Tigwa’s end-state role:
  - standing operational monitor;
  - authoritative task/workflow store;
  - dependency scheduler/orchestrator state;
  - production mitigator;
  - generic coding-worker pool;
  - autonomous canonical plan editor or merge authority.
```

Hermaroid is also transitional. Its value is as an interface/adapter while it is useful, not as a foundation to protect indefinitely.

### 3. Native control plane, not a stack of small harnesses

The useful long-term ownership boundary is:

```text
Own:
  packet contracts, approvals, authority boundaries, source provenance,
  state transitions, dependencies, specialist admission/resumes,
  invariants, traces/evidence, review/stitch/merge rules.

Borrow or replace:
  agent CLIs, UI clients, models, providers, frameworks, local/remote hosts.
```

This means “build our own” narrowly: own the small, understandable control plane and contracts, not an unnecessary replacement for every model client or coding tool.

## Current execution architecture

The current canonical direction in PP-WORKFLOW-001 and PP-ORCHESTRATOR-001 is compatible with this framing:

```text
PostgreSQL queue_jobs / state machine
  owns work lifecycle, dependency eligibility, and operational history.

Native workflow primitive
  adds dependency ordering through depends_on; existing handler_family
  supplies specialist routing; no competing DAG/state authority.

Native orchestrator
  consumes approved packets, uses dependency state and a qualified resource
  catalog, dispatches to specialist inboxes/worktrees, and retains evidence.

Specialist execution substrate
  can be tgw-coder, Aider, Claude Code, or another proven resource.
  It is isolated and bounded by a packet, contract, invariants, and review.

Dave
  remains product/authority/merge decision maker.

Tigwa EA/librarian
  helps prepare, explain, route, follow up, and assess the evidence;
  does not become a second queue authority.
```

## Plan assurance: the missing planning discipline

A big Master Plan or a flagship context window is not proof that all material work was considered. Plan assurance treats planning as an evidence-bearing pipeline.

```text
canonical source corpus
  → deterministic source manifest
  → anchored atomic obligation ledger
  → dependency/duplication/contradiction map
  → PP detail and execution packets
  → specialist and adversarial review
  → deterministic coverage/evidence checks
  → Dave decision/promotion
```

### Assurance claim

> A planning pass is complete only when every material obligation in its declared source corpus has a visible disposition.

Valid dispositions include:

```text
represented | packetized | implemented-verified | deferred | blocked |
superseded | rejected-with-rationale | unresolved / needs-Dave-decision
```

A model may not claim “that’s it” without a source manifest and coverage result.

### Core artifacts

```text
Source manifest:
  exact paths/URLs, hashes, anchors, retrieval/revision facts, source class.

Atomic obligation ledger:
  exact claim/anchor; kind; PP/program; destination; owner; disposition;
  acceptance/review gate; confidence/freshness.

Coverage matrix:
  source obligation → PP/packet/invariant/runbook/decision → evidence.

Contradiction/drift register:
  conflicting source/decision/code/test/live claims preserved side-by-side.

Execution packet manifest:
  objective, sources, non-goals, authority, dependencies, invariants,
  acceptance evidence, review route, stop/recovery rules.
```

### Model use: capability-first and chained

Use flagship reasoning deliberately at high-leverage joins, not as a replacement for the chain.

```text
Flagship reconciliation
  reads a frozen corpus/program cluster and finds cross-PP dependencies,
  contradictions, hidden assumptions, and weak packet boundaries.

Anchored ledger / dependency map / unresolved questions
  becomes inspectable handoff state.

Bounded specialists
  receive exact relevant sources plus the global map; run independent lenses.

Flagship or independent adjudication
  handles disputed/high-risk findings and cross-PP authority/recovery issues.

Deterministic checks
  validate anchors, coverage, dependencies, schema, tests, and evidence.

Dave
  sees only genuine remaining decisions and promotion/merge choices.
```

The ledger is an index into source, not a lossy replacement: any later worker can retrieve the exact original source when a conclusion is contested.

## Research capture: `best-project-plan-llm.txt`

The research was useful as hypothesis generation. Its durable contributions are:

```text
- use a source map before specialist work;
- specialize gap/duplication/security/review functions;
- chain outputs through explicit artifacts;
- use whole-context reasoning for reconciliation;
- use bounded packets for downstream workers;
- distinguish different model capabilities by step.
```

Grounding corrections:

```text
- Do not accept named-model recommendations as permanent fact or policy.
- Do not make DuckDB a task/workflow state authority; PostgreSQL remains so.
- Do not add CrewAI/LangGraph/etc. as a competing control plane.
- Do not make a graph database or agent framework the source of planning truth.
- Do not assume model agreement establishes truth; use source anchors,
  deterministic checks, live evidence, or a named human decision.
```

DeepSeek-R1 and GPT-4o/GPT-5o-class resources are candidates where their current route, cost, context, tool support, and observed fixture results match a step’s required capability. They are not permanent dependencies.

## Claude Code custom-subagent article

Source reviewed:
`https://www.digitalapplied.com/blog/build-claude-code-custom-subagent-step-by-step-2026`

Useful patterns:

```text
- narrowly scoped specialist definitions;
- fresh task context rather than inherited chat assumptions;
- least-privilege tool allowlists;
- fixed output formats;
- explicit main-session coordination;
- model selection by role/cost/risk.
```

For TGW, add an authority/stop-rule anchor to the article’s role/context/workflow/output pattern:

```text
Role:       narrow job owned by the resource
Context:    exact authoritative source packet
Workflow:   allowed ordered checks/actions
Output:     required evidence-bearing return artifact
Authority:  forbidden actions, human gates, and stop/escalate conditions
```

A Claude Code subagent is an implementation detail inside a qualified specialist, not the TGW orchestrator. It does not itself supply durable work state, source provenance, trace integrity, delivery acknowledgement, approval, or merge authority.

## Dave-to-machine communication contract

Dave speaks normally. Tigwa creates the structured record; Dave need not fill out a form.

```yaml
communication_id: provisional
kind: research-question | decision-proposal | planning-packet | execution-packet | review-request
from: Dave
captured_intent: >
  bounded statement of what Dave means/wants
outcome:
authoritative_sources: []
facts_with_evidence: []
candidates_and_hypotheses: []
constraints_and_non_goals: []
authority_boundary:
open_questions: []
decision_needed:
recipient_role:
required_output:
acceptance_evidence:
stop_and_escalate_when:
related_or_superseded_records: []
```

The communication must make clear what is verified, what is a candidate, what Dave has settled, and what cannot proceed without further evidence or a decision.

## Capacity and context tune-up

### Claude

Use clean, compact, packet-driven Claude sessions for bounded implementation/review. Do not resume a large exploratory session for unrelated execution.

The inspected TGW project `CLAUDE.md` is 31,757 bytes. It needs a review-only prune manifest before edits:

```text
Keep:
  enduring authority/safety rules, exact project commands, canonical paths.

Move/reference:
  historical narrative, PP-specific detail, long runbooks, temporary status,
  routing explanations, and implementation history.

Deduplicate:
  instructions already owned by canonical Plan Vault/repo documents.

Clarify:
  ambiguous rules that cause broad exploration or repeated re-asking.
```

Any shared CLAUDE.md change must be preserved/reviewed as a diff; shortness is not the only aim—precision is.

### Hermes

Current observed Hermes configuration before any change:

```text
Main model: gpt-5.6-terra via OpenAI Codex
Compression: direct DeepSeek V4 Flash
Web extraction: direct DeepSeek V4 Flash
```

Do not tune Hermes into a monitoring/control-plane substitute. For EA use, prefer a routine default model for ordinary grounded work and select flagship reasoning at the start of a new deep session for plan assurance, high-stakes reasoning, and adversarial review. Confirm exact live model IDs/terms before configuration; do not hardcode an assumed “GPT-5.5 Instant” identifier.

Avoid repeated mid-session model switches: they reset prompt caching and can force a costly re-read/compression. Start a dedicated deep session when that work begins.

## Tomorrow: bounded agenda

### A. Preserve mobility and recovery first

Dave is upgrading the Tigwa plan and moving the work to the laptop after its current backup and larger-SSD installation process.

No migration, installation, storage modification, or role cutover is implied by this brief. Before any move, establish a source/recovery manifest for the material that matters and confirm the destination/authority boundaries.

**Planned laptop-only EA capability — desktop/Waydroid visual inspection:**

```text
Purpose:
  Dave can deliberately show Tigwa selected native desktop applications,
  including the existing Waydroid camera app, for interactive inspection,
  research, mapping, and bounded guided operation.

Status:
  planned / missing; not configured or attempted on the current Nix host.

Minimum prerequisites:
  logged-in laptop graphical session; cua-driver installed and its daemon
  running in that interactive session; successful `hermes computer-use doctor`;
  deliberate selection of an app/window by Dave.

Boundary:
  this is an EA visual/interactive capability, not an unattended monitor,
  broad Android-control authority, or a substitute for authoritative app data.

Acceptance proof:
  capture the selected Waydroid camera-app window, identify its visible state
  and controls, perform only a Dave-authorized benign guided interaction, then
  verify teardown/revocation behavior.

**Candidate integration — Tasker inside Waydroid:**

```text
Question:
  Can Tasker provide useful, reliable automation around the Android camera app
  while both run in the same Waydroid container?

Status:
  partially proven in Dave's prior hands-on trial: Tasker installed in Waydroid
  and appeared to work. Coverage of the intended complete app set and reliable
  background/restart behavior remains unverified. Treat the remaining work as a
  small compatibility spike, not planned operational infrastructure.

Comparative option:
  the test contract is Android-automation-tool-neutral. If Tasker lacks a
  needed permission, integration, or reliability property, run the same small
  fixture against other Android automation candidates before choosing a
  long-lived interface.

**What must carry forward — existing Android control vocabulary:**

The purpose is not generic phone automation. The camera app must prove it can
join Dave's already-small control surface without creating a second command
language or a parallel dashboard.

```text
Keep / reuse if the fixture proves it:
  - Dave's small existing Android tool set;
  - the JSON envelope/building conventions he already uses;
  - remote command triggers and their acknowledgement/failure behavior;
  - the existing HUD as the human-visible state/control surface.

Camera-app adapter target:
  incoming existing JSON command → validated camera-app action → explicit
  success/failure/state event → existing HUD update.
```

The compatibility spike must start from representative real payloads and HUD
states, not from a new one-off Tasker demo. Record the existing command names,
required fields, authentication/authority expectation, idempotency behavior,
and the expected HUD result before changing the camera app.

Minimum end-to-end proof:

```text
1. A harmless existing remote JSON trigger reaches the Android control layer.
2. The camera adapter accepts only a valid, recognized command and returns a
   structured result; malformed/duplicate/unauthorized inputs fail visibly.
3. A Dave-authorized benign camera action occurs.
4. The established HUD reflects the resulting state and any failure.
5. Restart/offline/retry behavior does not silently duplicate a camera action
   or leave the HUD claiming a state it cannot substantiate.
```

Likely first-fit paths:
  time/intent/HTTP/profile actions inside Android; camera-app documented
  intents/plugins if the app exposes them.

Do not assume:
  host-Linux control, camera hardware access, boot persistence, screen-off
  reliability, notification delivery, accessibility/UI automation, or a
  camera-app control API. These each need direct proof.

Known risk to test:
  Waydroid may suspend its Android container after display timeout by default;
  Tasker itself requires foreground/background and battery/permission settings
  to remain active. Either can defeat unattended profiles.

Minimal proof sequence:
  1. Install/open Tasker and the camera app in the same Waydroid instance.
  2. Prove one harmless local time or button-triggered Tasker action.
  3. Prove one explicit app-to-app intent/API action, if the camera app offers it.
  4. Repeat with the Waydroid window inactive and after a controlled restart.
  5. Only then assess a narrow host bridge through an explicit supported channel.
```

### B. First planning deliverable: assurance pilot, not a rewrite

Pilot corpus:

```text
PP-WORKFLOW-001
PP-ORCHESTRATOR-001
PP-APPROVAL-001
PP-CLASSIFIER-001
PP-AGENTTRACE-001
```

Initial deliverables:

```text
1. Frozen source manifest with hashes/anchors.
2. Obligation-ledger schema and first extraction pass.
3. Cross-PP dependency, duplication, and contradiction register.
4. Plan → PP → packet coverage matrix.
5. A small set of genuinely packet-ready next actions.
6. Independent review asking what material source claim lacks a disposition.
```

Success is finding/retaining real hidden or contradictory material, not producing a prettier unverified rewrite.

### C. Claude hygiene, review-only first

Create a classified audit of the current project CLAUDE.md and any actual auto-memory sources found in the execution environment. Propose a minimal reversible diff; do not edit until reviewed.

### D. Tigwa EA consolidation map

Inventory current Hermes/Tigwa claims and classify each:

```text
KEEP AS EA/LIBRARIAN
TRANSITION TO EXISTING/NATIVE AUTHORITY
RETIRE
```

For every transition, identify the target authority, evidence needed to prove it works, fallback/rollback, and when the stopgap may safely be removed. Do not disable a current function simply because its replacement is planned.

## Decisions/gates for Dave

No decision is required tonight. Tomorrow’s decision-ready questions are:

```text
1. Accept the plan-assurance pilot corpus and initial artifact schema?
2. Approve a review-only Claude-context prune audit?
3. Confirm the laptop as the next Tigwa EA host only after backup/SSD/migration
   readiness is demonstrated?
4. Select the first high-value machine-readable communication type to prove:
   research question, decision proposal, planning packet, execution packet,
   or review request?
```
