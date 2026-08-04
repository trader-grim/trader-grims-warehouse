# Review: todo #1581 — agent-trace-phase2

Status: cleared
Reviewer: Claude (main session)
Branch: `todo/1581-agent-trace-phase2` @ (worktree HEAD, commit `b935042`) — 1 commit ahead of `catio-nix-0.0.1-alpha`, 0 behind

Checked against `docs/TGW-Plan-Vault/plan/packets/1581-agent-trace-phase2.md`'s
Spec/Out-of-scope/Acceptance sections and the 1581-RESULT.md manifest.

- Spec item 1 (`list_agent_runs()`): confirmed — capped at `limit=200`,
  most-recent-first, docstring explicitly names the cap as by-design (no
  silent-cap violation).
- Spec item 2 (`agent_trace_render.py`): confirmed — pure `build_agent_runs_doc()`
  / impure `render_agent_runs_doc()` split, atomic tempfile-write +
  `os.chmod(0o664)` + `os.replace()` byte-for-byte matching `plan_render.
  render_taskboard()`'s pattern, reuses `_plan_heading_map()` rather than
  reimplementing it, generated-file banner matches the specified style.
- Spec item 3 (`workers/agent_run_render.py`): confirmed — exact structural
  match to `workers/plan_render.py`, correctly self-documents the
  out-of-scope "no systemd unit yet" gap rather than omitting it silently.
- Spec item 4 (coalesced enqueue wiring): confirmed — `_enqueue_agent_run_render()`
  mirrors `_enqueue_plan_render()`'s shape (dedupe_key/not_before/max_attempts,
  bare `try/except: pass`), called after successful INSERT in `start_agent_run()`
  and after successful UPDATE in `end_agent_run()` — correctly placed AFTER
  the zero-rowcount `ValueError` check, so it never fires on a failed `end`.
- Spec item 5 (tests): 47 new/updated tests across both test files,
  independently re-run from the worktree — 47/47 pass. Full offline suite
  independently re-run — 2708 passed, 1 skipped, matching the manifest's
  claimed numbers exactly.
- Scope: all 7 touched files (`state_machine.py`, `agent_trace_render.py`,
  `workers/agent_run_render.py`, 2 test files, breadcrumb, RESULT.md) are
  within the packet's declared area. No systemd/flake/UI files touched
  (correctly deferred per Out-of-scope).
- Deviation (dropped the packet's proposed `cfg=None` leading parameter on
  `list_agent_runs()`): flagged in the manifest, well-justified — verified
  live that no function in `state_machine.py`, including `get_agent_run()`
  which this was placed next to, takes a `cfg` argument; the module uses its
  own `init()`-set DSN. Matching established local convention over the
  packet's literal proposal is the correct call here, not a silent
  substitution — properly flagged either way.
- Invariants: no relevant invariant violated. This is pure read/render work
  over Phase 1's existing `agent_runs` table — doesn't touch the
  self-attestation/integrity questions raised separately in the ongoing
  Tigwa review thread (PP-AGENTTRACE-001 Legs A/B/C), which is unrelated to
  this packet's scope.

No trigger fired. Cleared for stitch.
