# IN PROGRESS — orchestrator planning week (prep for Friday Max-plan decision)

**Started:** 2026-07-21

## What's happening

Dave is running a multi-day planning cycle: converge PP-WORKFLOW-001 /
PP-ORCHESTRATOR-001 / PP-APPROVAL-001 (all NEW 2026-07-21) — work through
gaps, fill them, iterate — targeting a fully-integrated, packet-ready plan
by Friday 2026-07-24, when he plans to buy a Max subscription and start
executing at much higher throughput, mostly via `tgw-coder`. Tigwa's
training regimen continues in parallel, faster.

## Where we are

- Ran a gap sweep on the PP-WORKFLOW-001/PP-ORCHESTRATOR-001/PP-APPROVAL-001
  cluster. Found the orchestrator's implementation vehicle was undecided
  (no concrete "what runs the orchestrator" answer) and that it wasn't
  connected to already-live infra (`tgw-aider` MCP bridge, `tgw-runner-
  review` skill).
- Dave resolved it: **no new service.** Specialist-roster-growth pattern —
  tgw-coder is specialist #1 (already trusted, already running this shape
  manually). Aider becomes specialist #2 once tgw-coder is comfortable,
  reusing the existing `tgw-aider` bridge. Every future specialist joins
  the same way, one at a time, never all at once.
- Dave added a further step: once the tgw-coder+Aider loop runs cleanly,
  spin off the repetitive orchestration mechanics themselves (the
  triage→dispatch→review→stitch loop I currently run by hand) — same
  trust-then-delegate discipline, one level up.
- Encoded all of this into `TGW-Master-Plan.md`'s `PP-ORCHESTRATOR-001`
  section (resolution + 6-step execution sequence).
- Opened todo #1626 (PP-WORKFLOW-001 Phase 1: `depends_on`/routing on
  `queue_jobs`) — the only step in the sequence that's genuinely new code
  and thus the only one that's packet-ready right now.

## Continued — same session, after the above

- Next gap: PP-WORKFLOW-001's `depends_on` had no failure-propagation
  design. Dave decided: **block indefinitely, no auto-cancel**, plus some
  form of resubmission (shape TBD) — split into todo #1627.
- Realized #1627 (resubmission) is really PP-APPROVAL-001's territory (a
  human decides retry/cancel/reassign on a stuck dependent) — retagged
  #1627 from PP-WORKFLOW-001 to PP-APPROVAL-001, marked blocked on that
  primitive existing.
- Scoped PP-APPROVAL-001's approval-type question: Dave decided **typed,
  config-driven handlers**, not a generic callback — same pattern as
  `tgw-models.json`. `flake_push`/`flake_switch` and `dependency_resubmit`
  named as the first two types to migrate onto it.
- That surfaced a bigger consolidation: TGW already has 4 independent
  guard-hook mechanisms (`flake-guard.py`/E10, `app-code-guard.py`/E12,
  `worktree-guard.py`/E11, `trace-immutability-guard.py`/E14) plus the new
  approval-type routing plus Claude Code's own built-in auto-mode
  classifier — 6 things all answering "what kind of action is this, what's
  allowed." Dave: "I like single tools... this is the perfect opportunity."
  Named this **PP-CLASSIFIER-001** — one unified, config-driven classifier,
  explicitly the concrete design for PP-CATIONIX-001's still-unbuilt
  "crypto-lock" permission architecture. Schema decided
  (`tgw-classifier.json`: type/match/scope_rule/enforcement/approval).
  Migration order: `flake-guard.py` first (lowest stakes, todo #1628) —
  deliberately NOT touching E11/E12/E14 until Phase 1 proves clean.
- All of this encoded into `TGW-Master-Plan.md` (PP-WORKFLOW-001,
  PP-ORCHESTRATOR-001, PP-APPROVAL-001, new PP-CLASSIFIER-001 sections) and
  into `handoff.md` (fresh snapshot written, old one archived to
  `archive/handoff-2026-07-21-statemachine-incident-carryforward.md` —
  note that archive still carries an UNRESOLVED item: the
  `nix-flake-maintainer` unauthorized-push finding from todo #1620 still
  needs Dave's decision, nothing this session touched it).

## Not yet done / next step

- No packets written yet for #1626/#1627/#1628 — all are design-decided,
  not build-ready.
- Steps 3-6 of PP-ORCHESTRATOR-001's sequence (formalize tgw-coder as
  specialist #1 in docs, add Aider as specialist #2, spin off orchestration
  mechanics) are named but not scoped into their own packets.
- PP-CLASSIFIER-001's migration-mechanics details (does a hook script call
  into the shared classifier, or get replaced outright?) still open.
- Still iterating per Dave's stated rhythm: small bounded questions, his
  weigh-in each round, encode, repeat — expect more gap-fill rounds before
  Friday 2026-07-24. Session ended here (Dave running errands/shipping).
