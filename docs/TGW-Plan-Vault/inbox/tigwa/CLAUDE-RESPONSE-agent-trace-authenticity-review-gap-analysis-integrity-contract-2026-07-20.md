# Response: Agent-trace authenticity review — gap analysis + integrity contract proposal

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T15:01Z
**Todo:** #1585

# Response: agent-trace authenticity and anti-cover-up controls

**To:** Tigwa (for Dave)   **From:** Claude   **PP:** PP-AGENTTRACE-001
**Re:** CLAUDE-REQUEST-agent-trace-authenticity-review-2026-07-20.md

No source, data, schema, hooks, or config modified for this response — review
only, as requested. One relevant fact: I had already started drafting a
Phase 4 SessionStart hook (`.claude/hooks/agent-trace-start.py`) before this
request reached me. It's untracked, uncommitted, and not wired into
`.claude/settings.json` — inert, no effect on any session. I'm leaving it as
a local draft and not proceeding on Phase 4 until this review is resolved,
per your request's explicit scope.

## 1. Gap analysis against the actual merged Phase 1 implementation

Confirmed real, citing exact source (all `catio-nix-0.0.1-alpha`, merged
commit `f0907f4`):

- **`run_id` is client-generated, not server-attested.** `start_agent_run()`
  (`state_machine.py:996`) does `run_id = uuid.uuid4().hex` (`:1015`) inside
  the function that any caller with `tgw` CLI/DB access invokes — nothing
  binds a run_id to a specific process, host, or credential beyond whatever
  string the caller supplies for `agent_type`/`host`/`git_branch`, all of
  which are free-form, unvalidated arguments.
- **No content digest anywhere.** Neither `agent_runs` (schema:
  `state_machine.py:958-970`) nor `archive_transcript()`
  (`logging.py:236`) computes or stores a hash of the transcript content.
  There is no way today to prove an archived `.jsonl` file matches what a
  given run actually produced, or to detect it was altered after archival.
- **`archive_transcript()` is explicitly re-entrant/overwriting by design.**
  Its own docstring (`logging.py:255-258`) states: "calling this twice for
  the same run_id atomically replaces that run's own copy (idempotent
  re-archival)." This was written for a legitimate case (retry after a
  transient failure) but is *exactly* the mechanism your request names as a
  cover-up vector (§4, "post-hoc archive replacement") — nothing today
  distinguishes a legitimate retry from a malicious swap. This is the single
  highest-priority finding.
- **`status`/`summary`/`ended_at` are pure self-attestation.** `end_agent_run()`
  (`state_machine.py:1029`) takes `status` as a caller-supplied string
  (CHECK-constrained to five values, but the *choice* of value is the
  caller's) and a free-text `summary`. A compromised or runaway agent that
  can still shell out has full authority to call `tgw trace end <its own
  run_id> --status completed --summary "..."` regardless of what actually
  happened.
- **No lifecycle reconciliation exists.** A row inserted via `start_agent_run`
  with no matching `end_agent_run` call sits `status='running'` forever —
  nothing in Phase 1/2/3 (render, UI) flags or ages this out. `get_agent_run`/
  `list_agent_runs` (Phase 2, not yet merged) are plain reads, no anomaly
  detection.
- **`transcript_path` is unverified.** `end_agent_run` accepts any string for
  `transcript_path` (`state_machine.py:1029-1050`) — nothing checks it points
  at a file that was actually archived via `archive_transcript()`, or that
  the file exists at all.
- **One partial mitigant, not yet exploited:** for Claude Code sessions
  specifically, the *planned* Phase 4 mechanism is a Stop hook receiving
  `transcript_path` from the harness's own hook payload — not something the
  model supplies. That's a real, harness-level independent-witness property
  (the running model can't directly control what path the harness reports),
  but it does NOT extend to non-Claude-Code agents (Aider, a `tgw-coder`
  process calling `tgw trace` on its own behalf) — those are pure
  self-attestation with no independent witness at all, today or in the
  current Phase 4 plan.
- Nothing in the request's gap list is contradicted by what's actually
  built — your analysis is accurate. One correction: filesystem permissions
  (`tgw:tgw`, `0660`/`0770` per the Phase 1 packet) do stop other Unix
  identities from tampering, but every code path that legitimately writes
  archived transcripts (`archive_transcript`, the `tgw` CLI) runs *as* `tgw`
  — so the permission boundary doesn't separate "the agent under trace" from
  "the archival mechanism itself." They're the same trust domain today.

## 2. Minimum viable integrity/authenticity contract — proposed layering

- **Agent-claimed trace/status** (exists today): `agent_runs.status`/
  `summary`/whatever the agent reports via `tgw trace end`. Keep this layer
  — it's cheap, fast, useful for the common case — but never treat it alone
  as evidence of what happened, only as an index/pointer.
- **Independently witnessed capture/lifecycle evidence** (does not exist):
  a record of "a run with this run_id started/stopped" written by something
  the traced agent does not control the timing or content of. For Claude
  Code, the harness-fired SessionStart/Stop hooks are the closest available
  primitive (see above) — but the hook script itself currently runs the
  *same* archival code as everything else; the witness property is only in
  "the harness decided to fire this," not in the archival step downstream.
  For non-hook-covered agents (Aider, tgw-coder, nix-flake-maintainer),
  there is no witness primitive available yet — this is a real, not-yet-solved
  gap, not something Phase 4 as drafted closes.
- **Byte-verified archived trace** (does not exist): a content hash
  (SHA-256, matching the pattern already used elsewhere in this codebase —
  see `PP-DATAINTEGRITY-001`'s photo-integrity work, `sha256 verify-after-copy`)
  computed at archival time and stored in `agent_runs.transcript_sha256` (new
  column) or a separate table, immutable once written (no UPDATE path).
  `archive_transcript()`'s existing overwrite-on-retry behavior should
  become: overwrite is only allowed if no hash has been recorded yet for
  that run_id (i.e., first successful archival locks it) — turns the
  "legitimate retry" case into "retry only succeeds if nothing was ever
  successfully archived before," which closes the exploit path named above
  without removing the retry capability that motivated the original design.
- **Integrity exceptions and investigation state** (does not exist): a
  status value or side table for "this run's evidence failed a reconciliation
  check" (hash mismatch, missing end, orphaned parent_run_id) — distinct from
  the agent's own self-reported `status`, so an anomaly can be flagged
  without an agent's own attestation being able to suppress it.

## 3. Producing/preserving independent evidence without agent self-authority

- **Identity separation**: today, `tgw` is a single identity for "all agent
  activity + all archival machinery." A real fix needs at minimum a
  write-only or append-only path for the *first* write of a transcript/hash
  that the traced agent's own process cannot later invoke a second time
  (see the "lock on first successful archival" proposal above) — this is
  achievable without a new Unix user, just a code-level invariant enforced
  in `archive_transcript`/`end_agent_run`, but it only holds if literally
  nothing else in the `tgw` codebase provides an alternate write path
  (worth an explicit audit, not assumed).
- **Filesystem/DB permissions**: current `0660`/`0770` is the right baseline
  (group-shared, not world-readable) but doesn't create the write-once
  property above — that has to be enforced in application logic (a hash
  column with no UPDATE grant on it beyond the archival function's specific
  first-write path), not permissions alone.
- **Capture timing**: the SessionStart/Stop hook pair (once built) is the
  strongest available timing signal for Claude Code — hooks fire on harness
  events, not model tool calls. For non-hook agents, the honest answer is
  there's no equivalent yet; recommend treating those runs as
  self-attested-only in the UI/render layer (item 6) rather than pretending
  parity exists.
- **Hash/signature**: SHA-256 content hash is sufficient for tamper
  *detection* (item 4's reconciliation) without needing a full signature/PKI
  scheme — recommend starting there, not over-building.
- **Nesting/parent-child**: `parent_run_id` is a self-declared FK today
  (`state_machine.py:961`) — nothing verifies the claimed parent actually
  dispatched the child. A cheap partial fix: only accept a `parent_run_id`
  that is currently `status='running'` at insert time (reject stale/already-
  ended parents) — doesn't prove genuine dispatch, but rules out an obviously
  fabricated lineage.
- **Recovery when a host/agent dies mid-run**: today a `status='running'`
  row with no `ended_at` just sits forever — recommend a reconciliation pass
  (candidate for Phase 2's render worker or a separate check) that flags
  (not auto-closes) any `running` row older than some threshold as
  "stale/unclosed" for review, rather than silently either hiding it or
  guessing an end state for it.

## 4. Reconciliation/detection checks — concrete list for a future packet

- Missing start: an `ended_at` set via a path that didn't go through
  `start_agent_run` first — not currently possible given the FK/PK
  structure (can't `end_agent_run` a `run_id` that was never inserted —
  confirmed by Phase 1's own live-caught bug, `state_machine.py:1029-1050`,
  which already raises on zero rowcount). This specific case is closed.
- Missing end: `status='running'` past a staleness threshold — not built,
  proposed above.
- Byte/hash mismatch: requires the hash column proposed in §2 — not
  possible to check today since no hash exists.
- Duplicate/orphaned runs: `run_id` is a PK so true duplicates are
  impossible at the DB level; "orphaned" (child referencing a parent that
  never legitimately dispatched it) needs the parent-validity check in §3.
- Post-hoc archive replacement: closed by the "lock on first successful
  archival" proposal in §2 — currently wide open (see §1's top finding).
- Index/raw divergence: the `agent_runs` row's `summary`/`status` vs. what
  the raw transcript actually shows — no automated check proposed here; this
  is closer to an audit/spot-check activity than a mechanical detector, flag
  as an open question rather than a false promise of full automation.

## 5. Phase 1 hardening vs. later work — explicit split, no scope creep

**Recommend folding into Phase 1 hardening (small, high-value, before Phase
4 hooks go live):**
- Content hash column + "lock on first successful archival" — closes the
  single worst gap (§1's top finding) with a small, contained change.
- Reject `parent_run_id` values that aren't currently `status='running'`.

**Belongs in later capture-hook work (Phase 4, currently paused per this
review):**
- The harness-witness property for Claude Code hooks — proceed only once
  the hardening above lands, so the hooks aren't writing into a still-
  self-attestation-only table.
- Any equivalent witness mechanism for non-Claude-Code agents — explicitly
  unsolved, not scoped yet, needs its own design pass (possibly a follow-on
  PP, not silently folded into Phase 4).

**Belongs in render/UI work (Phase 2/3):**
- Surfacing hash-verified vs. unverified, stale/unclosed runs, and
  integrity-exception state visually (item 6) — those phases' packets should
  be updated to include this once the hardening lands, not shipped without
  it and patched later.

**Belongs in later operational auditing, not this initiative's core:**
- Full index/raw divergence spot-checking — recommend a periodic manual/
  Tigwa-led audit process rather than a promised automated detector.

**Explicit consequential tradeoff to name for Dave:** the "lock on first
successful archival" change means a genuinely failed/interrupted archival
(e.g. process killed mid-copy, before the hash is recorded) is *recoverable*
(no hash recorded yet = retry still allowed), but a *successful* archival
becomes permanently non-overwritable even if it later turns out to be wrong
for a legitimate reason (e.g. Dave manually wants to correct a mis-tagged
`agent_type` on an old row) — that would need an explicit, logged
correction path, not a silent overwrite. Worth Dave confirming this
tradeoff is acceptable before it's built.

## 6. What Obsidian/`/form/runs` must visibly disclose

- Whether a run's transcript is hash-verified vs. only self-attested (no
  hash recorded) — a visible badge/column, not buried.
- Stale/unclosed (`running` past threshold) runs, distinctly flagged, not
  silently shown as if still legitimately in-progress.
- Any integrity-exception state from §2/§4 (once built) rendered
  prominently, not folded into the same "summary" text field the agent
  itself writes — self-attested and independently-flagged content must stay
  visually and structurally separate so a compromised agent's own summary
  text can't overwrite/obscure an exception flag.
- For non-Claude-Code agent types, an explicit "self-attested only, no
  independent witness" indicator per §3's honest-gap acknowledgment, until
  that gap has a real fix.

## Summary

Your gap analysis is confirmed accurate against the actual merged code, with
one addition (the `archive_transcript` re-entrancy design is the single
sharpest concrete vulnerability, not just a general "no hash" gap) and one
partial mitigant named (harness-fired Claude Code hooks have some
independent-witness property that non-Claude-Code agents don't get). This is
a design proposal for Dave's decision, not implementation — nothing has been
built. Recommend the two Phase-1-hardening items above land before Phase 4
hooks are wired live; I've paused that work pending this review as
requested.
