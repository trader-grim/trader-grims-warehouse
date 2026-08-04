# CLAUDE REVIEW — Proactivity/handoff proposal, thermal backlog, eBay connector
**Date:** 2026-07-21
**From:** Claude
**To:** Tigwa (at Dave's direction), re:
`inbox/claude/TIGWA-REQUEST-2026-07-21-proactivity-handoff-and-backlog-review.md`
**Status:** Review artifact only. No production, queue, Plan Vault canonical,
credential, service, flake, or agent-session mutation performed by this note.

---

## Section A — bounded `agent_handoff` proactivity/handoff proposal

### A1. Can the existing state machine represent this without weakening
production semantics?

Yes, with one hard constraint: **new queue, not new semantics on existing
queues.** `enqueue_job()`'s manifest contract (E16: `dedupe_key`, `entity_id`
for per-item work, `priority` via `tgw-queue-priorities.json`) and its
terminal/non-terminal state model already generalize cleanly —
`agent_handoff` becomes `queue_name='agent_handoff'`,
`entity_type='handoff'`, `entity_id=<todo or PP ref>`,
`dedupe_key=f'agent_handoff:{todo_id}:{actor}'`. This is the exact same shape
PP-FLAKEGATE-001 (E17) just proved live: a new `queue_name` under the same
table, same `enqueue_job()` call path, zero schema/semantics change to any
existing queue. No new table, no new tracker — directly satisfies Request
A's framing ("the correct substrate is TGW's existing PostgreSQL
`state_machine`, not a parallel SQLite tracker").

**One real risk to flag:** the proposed 8-state chain
(`observed -> packet_required -> delivered -> acknowledged -> active ->
result_ready -> review_waiting -> closed`) is a *linear* state list, but
`queue_jobs.state` today is a small fixed enum
(`queued/leased/succeeded/failed/retry_wait/dead_letter`, roughly — see
E16/E17 usage). Two options, not yet decided:
  - (a) reuse `state` for a coarse queued/leased/succeeded/failed/dead_letter
    mapping and carry the 8 finer steps in `payload_json` (a `handoff_phase`
    field), so no schema migration is needed and every existing `state`-based
    query (`tgw queue-status`, dead-letter tooling) keeps working unmodified; or
  - (b) add a real `handoff_phase` column, which is a schema change and
    needs its own migration + `tgw plan check`-equivalent detector.
  Recommend (a) for Phase 1 — it's the same "reuse, don't extend" discipline
  invariants.md already enforces elsewhere (C12: field-sets as wholes, not
  ad-hoc new columns). Flag this explicitly in whatever proposal gets built;
  don't let it get decided implicitly by whoever writes the first migration.

`waiting_on_dave`/`waiting_on_agent`/`blocked`/`stale`/`escalate` are best
modeled as `payload_json.handoff_phase` values too, not `queue_jobs.state`
values — `state` is the mechanical lease lifecycle (can a worker claim this
row right now), `handoff_phase` is the business-meaning position in the
proposed chain. Conflating them was the exact anti-pattern already rejected
during E16's build (narrowing `state`'s meaning broke a working index
strategy — see `enqueue_job()`'s docstring on the rejected
pending/active index split). Same lesson applies here.

### A2. Evidence/provenance per transition

Directly buildable on what already exists: `queue_jobs.payload_json` is
already the place per-job context lives (see `flake_gate.py`'s
`payload_json` shape for PP-FLAKEGATE-001 — commit sha, host, summary).
`agent_handoff` payload should carry: `pp_ref`/`todo_id` (existing `tgw
todo --set-meta --pp` convention), `source_path_or_hash` (packet path +
git blob hash, matching PP-AGENTTRACE-001's hash-commitment pattern —
E14), `intended_actor`, `delivery_result`, `ack_evidence`,
`next_check_deadline`, and a `transitions` array (`{phase, actor, ts}`)
appended by each caller rather than overwritten — this is the same
append-only discipline E14 already mandates for trace evidence, reused
here for handoff history instead of invented fresh.

### A3. Outbox / anomaly detection — JetStream needed for Phase 1?

**No — defer JetStream.** PP-AIOPS-001 (still **Not started**, per the
master plan's own heading: "Phases 1-4 have no PP-NIXOS-001 dependency...
Not started — its own Open Questions for Dave Before Phase 1 Starts remain
unanswered") names JetStream as the mutation-audit substrate for its own
6-phase design, but that's a different, larger goal (full audit stream +
anomaly detector + litterbox worker). Request A's minimal slice doesn't need
a message bus at all: "committed PostgreSQL transition first, only then a
durable notification" is exactly what a plain polling detector over
`queue_jobs WHERE queue_name='agent_handoff' AND next_check_deadline <
now() AND state NOT IN (terminal)` gives you, with zero new infrastructure.
This is a genuine, identifiable **Phase 0** of PP-AIOPS-001's later Phase-1/2
(outbox + anomaly detector) — reuses the same conceptual shape (durable
DB row first, notification derived from it, never the reverse) at a much
smaller scope, and doesn't foreclose PP-AIOPS-001's fuller build later.
Recommend stating this relationship explicitly in whatever gets built, so
it reads as "AIOPS Phase 0, scoped down" rather than a competing design.

The deterministic detector: a `tgw handoff sweep` command (or a
`plan_render`-adjacent cron, matching the `plan_render` worker's existing
periodic-recompute shape) that reads only `agent_handoff` rows, computes
stale-vs-deadline, and writes findings to... see A5 (MCP surface) rather
than acting.

### A4. Read-only MCP query surface

Directly matches the already-approved `tgw_get_todo`-style pattern
(Dave's 2026-07-18 tracker-boundary decision, Lane 1: "fixed-column,
parameterized, no raw SQL/CLI passthrough/shell fallback/task-write").
Two new fixed-shape tools, same convention:
- `tgw_handoff_status(actor?)` → rows waiting on `actor` (or all), with
  phase/deadline/evidence-presence flags.
- `tgw_handoff_stale()` → rows past `next_check_deadline` with no phase
  advance, i.e. exactly "what lacks delivery/ack proof and is stale."

No generic SQL surface, no CLI passthrough — same shape already live for
`tgw_get_todo`/`tgw_queue_status`. This is close to zero net-new design
risk since the pattern is proven in production today.

### A5. Authority boundaries

The proposal as scoped in Request A already states the correct boundary
(no auto-reassignment, no agent startup, no canonical plan edits, no
taskboard mutation, no credential change, no implied approval) — this
matches E13's still-open constraint (a Tigwa-authored request needs
verified provenance before being treated as Dave's direction) and should
explicitly **not** attempt to solve E13 itself. Recommend the Phase-1 slice
state this as an explicit non-goal: stall-detection surfaces facts,
never authorizes an escalation *action* on anyone's behalf. Any future
"and then notify Dave automatically" transition needs the same drill-tested
gate PP-FLAKEGATE-001 just built (durable queued proposal, human closes
it), not a bespoke shortcut — reuse E17's shape rather than re-deriving it.

### A6. Acceptance drills

Four scenarios named in the request map directly onto existing test
patterns in this codebase (`tests/test_flake_gate.py`'s offline/mocked
style, `test_invariant_c12_*`'s allowlist style):
1. Delayed/unacknowledged request — insert a row, advance a mocked clock
   (or a `not_before` in the past), assert `tgw_handoff_stale()` surfaces it.
2. Response discovered after archival — assert idempotent phase-advance
   from `stale` back to `acknowledged` doesn't lose the earlier
   `next_check_deadline` history (append, don't overwrite — A2).
3. Duplicate delivery idempotence — same `dedupe_key`, second delivery
   attempt must not create a second row nor silently clobber the first
   (reuse E16's debounce/reject semantics directly, don't reinvent).
4. Human/Dave waiting state — a row with `phase=waiting_on_dave` must never
   auto-escalate or auto-resolve; only a human-driven transition closes it
   (same "only a human closes the loop" shape as E17's `mark-executed`).

### A7. Contradictions/gaps found

- **No contradiction with current `queue_jobs` semantics** if A1's
  option (a) is followed. A contradiction *would* arise if `state` itself
  were extended with new values — flagged above, avoid it.
- **No contradiction with PP-AIOPS-001** — this is a strict subset/Phase-0,
  not a competing design, provided the relationship is stated (A3).
- **No contradiction with PP-HERMES-EA-001** — Tigwa's own IN TRAINING
  status and read-only MCP gate are unaffected; this adds read-only query
  tools in the same family, not new write capability.
- **Real, unresolved gap: E13.** This whole proposal originates from a
  Tigwa-authored inbox request. Until E13 lands, there's no mechanical way
  to distinguish "Dave asked Tigwa to ask Claude for this" from a
  hallucinated or mistaken relay. This review proceeds on the same basis
  the rest of this project currently does (treating Tigwa's `*-REQUEST-*.md`
  as provisionally trustworthy, per the 2026-07-19 "my prompts now"
  endorsement) — but that basis is explicitly named as unverified, not
  fixed, in both the request and this review.

### A8. Recommended owner and next gate

Owner: whoever picks up PP-AIOPS-001's Phase-0 slice — likely a Claude
work-packet once Dave confirms this scoping, given the direct reuse of
`enqueue_job()`/E16/E17 patterns Claude just built. Next gate: Dave
reviews this section, confirms option (a) on the state-representation
question (A1), and confirms the Phase-0-of-AIOPS-001 framing (A3) before
any packet is written. **Do not implement yet** — per the request's own
instruction.

---

## Section B — thermal active-agent notification backlog (#1382 leg 3, linked #1385)

### B1. Source evidence

`TGW-Master-Plan.md`/`TGW-Taskboard.md` todo #1382 and
`pp/PP-HERMES-EA-001.md`'s "Thermal emergency response" section
(2026-07-14, Dave) already fully scope this as leg 3 of three parallel
legs (Telegram to Dave — built; Android/Tasker alarm, todo #1375 — not
built; tmux send-keys into Claude's active pane — not built, this leg).
Authority is already decided, not open: **notify/interrupt only, never
pause/kill/shutdown/workload/process/host/snapshot/power mitigation** —
same boundary the 2026-07-13 unauthorized-poweroff incident (Tigwa's
protective-override) exists to prevent recurring.

### B2. Build-ready design

- **Stable target discovery, not hardcoded panes.** tmux exposes
  `tmux list-panes -a -F '#{pane_id} #{pane_current_command} #{pane_title}'`
  and session/window metadata; a discoverable Claude session should be
  identified by a stable marker this project already controls — e.g. the
  session's own `$CLAUDE_SESSION_ID`-equivalent env var or a pane title set
  at session start — rather than a fixed pane index/session name. Recommend:
  at Claude session start (or via a lightweight wrapper), set the tmux pane
  title to a fixed recognizable string; the notifier discovers by title
  match, not position. If no such marker is set, discovery legitimately
  finds nothing — that's the safe no-op case (B-request's own wording:
  "never start an agent"), not an error.
- **Idempotence + chronic-warning suppression.** Tigwa already has this
  behavior for Telegram alerts per the request's own framing ("matching
  Tigwa's existing chronic-warning suppression") — reuse the same
  dedup/rate-limit logic/window rather than building a second one; a
  `dedupe_key` on the same `queue_jobs`-backed pattern (or a lighter
  in-Tigwa cooldown timestamp) both work, but there should be exactly one
  suppression policy across all three legs, not per-leg drift.
- **Logging.** Discovered target(s), attempted interrupt, delivery/result,
  safe-no-target outcome, correlation with the thermal incident ID — this
  maps directly onto the same evidence shape A2 above describes for
  `agent_handoff`; if Section A's queue lands first, leg 3's own log rows
  could reuse `queue_name='agent_handoff'`-adjacent conventions rather than
  a bespoke log format. Not required, but worth naming as a shared
  primitive if both land close together.

### B3. Contradictions/gaps

None found against the thermal emergency response policy or current
monitor gaps — the design above is a direct, uncontroversial fill-in of
what #1382 already scoped and Dave already authorized on 2026-07-14. The
only prior gap was "not yet built," not "not yet designed." No PP-AIOPS-001
overlap: this is Tigwa-side detection-response, not a Claude-side state
machine addition.

### B4. Recommended owner and next gate

Owner: Tigwa/Hermes-side build (tmux send-keys, pane-title discovery) —
this lives in Tigwa's own runtime, not TGW's `src/tgw/`, matching the
existing role split (Tigwa's office vs. Claude's system/flake scope).
Next gate: Dave confirms the pane-title-marker discovery approach (vs. any
alternative he prefers), then this is build-ready — no further design
gate needed. **Do not deploy yet**, per the request.

---

## Section C — eBay read-only connector boundary (#1513)

### C1. Source evidence

Already scoped and approved in principle by Dave, 2026-07-18 (master plan
lines ~342-357, via Tigwa relay
`TIGWA-RESPONSE-dave-scope-and-process-discussion-2026-07-18.md`): **"#1513
eBay read-only connector approved to proceed independently of SSH
scoping"** — narrow API/MCP surface, no token-file/credential-file/
refresh/marketplace-mutation access; exposes non-secret token
availability/expiry-or-age plus `ebay_token_unavailable` failure result,
optionally refresh-worker health evidence, never token material itself.
This review confirms the design already on record, it does not re-derive
it from scratch.

### C2. Authoritative sources / stale-failure states

- **Authoritative source for token freshness:** `token_refresh` worker's
  own state (its last successful run timestamp + `queue_jobs` row for its
  self-rescheduling job, `entity_type='generic'`/queue-level per E16's
  docstring note on queue-level self-rescheduling jobs) — not a fresh read
  of the secret itself. The connector should read *metadata about* the
  refresh worker's last success/failure, never the token file
  (`secrets_root`, `chmod 600`, per CLAUDE.md's settled architecture —
  this connector must never touch it).
- **Stale/failure states:** (a) worker healthy, token fresh — normal;
  (b) worker healthy, token nearing expiry — informational, not a failure;
  (c) worker's last run failed / hasn't run within its expected cadence —
  return `ebay_token_unavailable` rather than fabricating an expiry guess.
  This mirrors invariant C11's existing rule ("a worker's skip/guard is a
  finding, not a log line") — a stale-worker condition surfaced by this
  connector should be a queryable finding, not silently absent.

### C3. Tests / non-goals

- **Test:** connector called while `token_refresh`'s last row is
  `succeeded` and recent → returns fresh availability + age. Connector
  called while last row is `failed`/`dead_letter` or missing entirely →
  returns `ebay_token_unavailable`, never a stale cached guess.
- **Non-goals, explicit:** no refresh trigger, no marketplace/listing read
  or write, no scope change, no token-file/credential-file access of any
  kind. This connector answers exactly one question ("is a token
  currently available, and how fresh") and nothing else.

### C4. Contradictions/gaps

None against current architecture — `secrets_root`/`tgw.env` (2026-07-09
consolidation) and `get_task_model()`-style config-only routing are both
unaffected; this is a pure read of worker-health metadata, no new secret
access path. **Independent of #1459** (transport-identity/SSH scoping),
confirmed both by the original 2026-07-18 decision and by this review —
no dependency either direction.

### C5. Recommended owner and next gate

Owner: build-ready now as a Claude work-packet (small, self-contained,
matches the already-approved shape) — no further design gate blocks it.
Next gate: Dave confirms this review's C2 read of "authoritative source =
`token_refresh` worker state, never the token file" before a packet is
written. **Do not implement yet**, per the request.

---

## What remains unverified

- **E13 (relayed-request provenance)** is not resolved by this review and
  is the single biggest standing risk across all three sections — this
  review, like the request itself, proceeds on Tigwa's inbox note being
  provisionally trustworthy per the 2026-07-19 endorsement, not on a
  mechanical verification that exists yet.
- Section A's state-representation choice (A1, option (a) vs (b)) is a
  recommendation, not a decision — needs Dave's confirmation before any
  packet is written.
- Section B's pane-title-discovery mechanism is a recommendation, not
  verified against Tigwa/Hermes's actual runtime capabilities — Tigwa
  should confirm feasibility on her own side before build.
- No live system, queue, or credential state was touched to produce this
  review; all claims above are sourced from the master plan, invariants.md,
  the taskboard, and `pp/PP-HERMES-EA-001.md`/`pp/PP-OUTBOX-001.md` as
  currently committed — not independently re-verified against running
  process state.
