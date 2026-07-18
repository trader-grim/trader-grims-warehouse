# Result: 1520 plan-brief-refactor

Status: done
Todo: #1520   PP: PP-KNOWLEDGE-001 (#1439)

## Files touched

- `src/tgw/plan_render.py` — added `plan_brief(cfg, pp_ref)` (pure, read-only
  helper) plus its private internals `_canonical_plan_source()` /
  `_plan_heading_sections()`, and module constants `PLAN_BRIEF_VERSION`,
  `PLAN_BRIEF_MAX_SOURCE_BYTES`, `PP_IDENTIFIER_RE`.
- `src/tgw/mcp_server.py` — `tgw_get_plan_brief` now delegates to
  `tgw.plan_render.plan_brief(cfg, pp)`; removed the duplicated
  parser/retrieval logic and the hard-coded `_PLAN_VAULT_ROOT` /
  `_PLAN_PACKET_VERSION` / `_PLAN_PACKET_MAX_SOURCE_BYTES` /
  `_PP_IDENTIFIER_RE` module constants (now unused there); dropped the
  `hashlib`, `re`, `datetime`/`timezone` imports that were only needed by
  the moved code.
- `tests/test_plan_render.py` — new test-first coverage for `plan_brief()`:
  invalid identifier, canonical-plan-unavailable, pp-not-found, ambiguous
  match, exact section line/byte boundaries + source/section SHA-256s,
  lowercase-PP normalization, the cross-reference/folded-PP
  false-ambiguity regression (carried over from Tigwa's v1), oversize
  section, linked-detail absent, linked-detail metadata-only (never
  inlined, item 4), and a read-only/no-writes guarantee test.
- `tests/test_mcp_server.py` — rewrote the two existing `plan_brief` tests
  to patch `mcp_server._cfg` (with `plan_vault_path`/`plan_master_path`)
  instead of the now-removed `mcp_server._PLAN_VAULT_ROOT`, updated the
  first test's assertions for metadata-only linked-detail behavior, and
  added a new FastMCP-boundary test using
  `mcp_server.mcp._tool_manager._tools["tgw_get_plan_brief"].run({"pp": ...})`
  (item 6; matches the existing convention used by `tgw_get_todo` /
  `tgw_add_suggest` / `tgw_mailbox_send` boundary tests in that file).
- `docs/TGW-Plan-Vault/plan/packets/results/1520-RESULT.md` — this file.
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1520-plan-brief-refactor.md`
  — recovery breadcrumb (worktree-local).

## Live evidence

**Pure-move verification (Master Plan section/canonical_source contract) —
byte-identical before vs. after the refactor:**

Ran the pre-refactor `tgw_get_plan_brief` implementation (checked out from
`catio-nix-0.0.1-alpha` at `src/tgw/mcp_server.py`, loaded standalone) and
the post-refactor `plan_render.plan_brief()` against the real, live
`docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` for `PP-KNOWLEDGE-001`:

```
Before (old mcp_server.py, base branch):
  canonical_source.sha256 = 8c0550e86ab95ee6c0f7411b94773891bdec587d6d97c325f618cf58699861a8
  section.line_start/line_end = 1026 1216
  section.sha256 = 213d3f41ca7b4e88fd4b13be72dddedb794ea8107bc01c23fbe50f03d17081b8

After (new plan_render.plan_brief(), this branch):
  canonical_source.sha256 = 8c0550e86ab95ee6c0f7411b94773891bdec587d6d97c325f618cf58699861a8
  section.line_start/line_end = 1026 1216
  section.sha256 = 213d3f41ca7b4e88fd4b13be72dddedb794ea8107bc01c23fbe50f03d17081b8
```

Identical in every field — same source SHA-256, same line/byte anchors,
same section SHA-256. Also matches Tigwa's own live SSH→MCP evidence from
her submission verbatim (source SHA `8c0550e8...`, lines 1026-1216). The
`linked_pp_detail` field is unaffected for this PP either way since
`plan/pp/PP-KNOWLEDGE-001.md` does not exist (`status: absent` in both).

**FastMCP tool-dispatch boundary (`tool.run()`), in-process against this
worktree's code:**

```python
tools = mcp_server.mcp._tool_manager._tools
tool = tools["tgw_get_plan_brief"]
out = json.loads(asyncio.run(tool.run({"pp": "PP-KNOWLEDGE-001"})))
# -> ok=True, canonical_source.sha256=8c0550e8...699861a8, section 1026-1216
```

Confirms via the actual MCP tool invocation path (not just a direct Python
call) that the delegation works end to end.

**Live SSH round trip (a1131 → tgw-prod, as Tigwa did for v1):** not
reproduced this run. It would require restarting the production Hermes
gateway's `tgw` MCP link against this branch's code, which is out of scope
for a task-branch worktree (never touches the deployed/shared checkout or
running services per this contract, and no packet authorization was given
for a production service restart). The FastMCP `tool.run()` boundary test
above plus the byte-identical old-vs-new comparison against the real live
Master Plan file are the load-bearing evidence instead — they exercise
the same parser/retrieval code path Tigwa's SSH round trip exercised,
just without restarting the deployed gateway.

**Full test suite:**

```
tests/test_plan_render.py + tests/test_mcp_server.py: 83 passed
Full suite: 2538 passed, 1 skipped, 0 failed (181s)
```

Run with
`LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH`,
confirmed `tgw.mcp_server.__file__` / `tgw.plan_render.__file__` resolved
under the worktree path (not the shared checkout) before trusting the run.

## Deviations from spec

- **Item 4 is itself a deliberate behavior change from Tigwa's v1**, not a
  deviation from this packet's spec — the packet explicitly requires
  linked `plan/pp/<PP>.md` documents to become metadata-only (no inlined
  content ever, even under the 64 KiB cap), whereas v1 inlined content
  when small enough. Implemented exactly as item 4 specifies. Flagging it
  here because it is a real, intentional behavior change to
  `tgw_get_plan_brief`'s output shape for any PP that *does* have an
  existing small detail doc (none currently do, so no live caller is
  affected today) — any future consumer expecting inlined
  `linked_pp_detail.content` needs to read the file at
  `linked_pp_detail.path` directly instead.
- No CLI command, CLAUDE.md/startup-contract change, or scope broadening
  beyond items 1-6 was made (per item 7 / "Do NOT do" in the packet).
- Everything else matches the packet's numbered spec (items 1-6) exactly;
  no other silent substitutions.

## Out-of-scope findings filed

None. No new operational friction, permission mismatch, or adjacent bug
was hit during this task.

## Answers to Tigwa's 4 review questions (from her v1 submission)

**1. Does the v1 source/provenance contract preserve Master Plan authority
correctly?**

Yes, unchanged by this refactor and reconfirmed live: every response still
carries the canonical Master Plan's own path/SHA-256/byte-count/mtime plus
the exact matched section's line/byte anchors and section SHA-256 — the
Master Plan file itself, not a summary or cache, remains the sole source
of truth, and this packet returns literal Markdown, never model-generated
text. The one behavior change (item 4, linked-detail now metadata-only)
*strengthens* this property rather than weakening it: a linked
`plan/pp/<PP>.md` detail document can never be silently presented as if it
were part of the canonical-Master-Plan-sourced packet — callers must go
read it directly, with its own hash/size as provenance, never blended into
the same trust tier as the Master Plan section.

**2. Does the proposed shared-helper refactor preserve one deterministic
implementation for MCP and possible future CLI use?**

Yes — that was the point of moving it. `plan_brief(cfg, pp_ref)` in
`tgw/plan_render.py` takes only a `cfg` dict and a PP string, has no MCP
dependency, and returns a plain dict (JSON-serializable). `mcp_server.py`
now just calls `json.dumps(plan_brief(cfg, pp))`. A future `tgw plan
brief` CLI command (item 7, explicitly *not* built in this packet) would
call the exact same function and get identical results — there is only
one parser/retrieval implementation in the codebase now, not two to keep
in sync.

**3. What live status source, if any, should a later packet include
without establishing stale state as a second truth?**

Not built in this packet (out of scope per items 4/7 and the "Do NOT do"
list — no queue-state or plan-status changes were authorized here).
Recommendation for whoever scopes that follow-up: `tgw.plan_render` already
has exactly this shape of live-but-separate status data in
`plan_status(cfg, pp_ref)` (open/done/blocked counts + latest-activity
todo, queried live from `todo_items` each call, never cached/rendered to a
file) — the safest pattern is to have a future `plan_brief` extension call
`plan_status(cfg, pp_ref)` itself at request time and attach it as a
clearly-labeled `live_status` sub-object with its own "queried_at"
timestamp, distinct from `canonical_source`/`section` provenance, rather
than folding tracker data into the Master-Plan-sourced fields. That keeps
the Master Plan the sole source of *planning* truth and the tracker the
sole source of *tracked-task* truth, with the packet just aggregating a
live read of both at response time (never persisting/caching either).

**4. What representative task set and acceptance criteria should govern
the parallel startup trial?**

Not built or run in this packet — Tigwa's non-goals/gates explicitly say
"before any startup-contract change, run representative tasks in parallel
... and report any authority gaps to Dave," and this packet made no
startup-contract change, so that trial is still a separate future step for
whoever owns that decision (not this executor's call — Dave/Tigwa per the
branch-review contract). Noting it here only because the question was
asked and deserves a written non-answer rather than silence: a reasonable
starting shape (not adopted, just proposed for review) would be 3-5 real
recent PP-scoped tasks of varying size, run twice each — once with the
agent given only `tgw_get_plan_brief(pp)` output as plan context, once with
the full Master Plan loaded as today — with acceptance judged on (a)
whether the agent's resulting diff/decision matched what a full-plan read
would have produced, and (b) whether the agent ever hit one of the
packet's own built-in warnings (ambiguous/oversize/broad-planning) and
had to escalate to a full-plan read anyway. That's a design question for
Dave/Tigwa, not something this task branch decided.
