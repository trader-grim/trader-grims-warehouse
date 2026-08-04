# Request #1440 — retrieval-first Plan Vault context packet

**Owner:** Claude  
**PP:** PP-KNOWLEDGE-001  
**Requested by:** Dave via Tigwa, 2026-07-15  
**Purpose:** Reduce Tigwa and Claude recurring startup/rediscovery burden while retaining the Master Plan as the only canonical authority.

## Decision boundary

All incoming material is knowledge first. Preserve source and provenance; do not make a source invisible because it is not immediately actionable.

The Master Plan remains canonical. Do **not** replace it with a generated summary, modify it, or change `CLAUDE.md` startup instructions in this task. This task establishes a small deterministic retrieval primitive which can be trialed and reviewed before either agent’s startup contract changes.

## Implement

Add a read-only command:

```text
tgw plan brief PP-KNOWLEDGE-001
tgw plan brief PP-KNOWLEDGE-001 --json
```

Use the existing `tgw.plan_render` / `tgw plan` structure where appropriate. It should generate a bounded, exact-source context packet from the current Master Plan and live tracker state.

### Required text packet fields

```text
- canonical master-plan path
- full SHA-256 of the master plan at read time
- requested PP ref
- exact matched heading
- inclusive source line range
- exact, unmodified Markdown of that PP section only
- live `plan_status` information for the requested PP, with an explicit
  status/error indication if the tracker is unavailable
- explicit warning that the packet is a bounded source view and agents must
  read the full Master Plan for broad planning, reconciliation, audit,
  ambiguity, or Dave-directed review
```

### Required JSON packet fields

JSON is for machine consumers and must include, at minimum:

```json
{
  "ok": true,
  "master_plan_path": "...",
  "master_plan_sha256": "...",
  "pp_ref": "PP-KNOWLEDGE-001",
  "heading": "...",
  "start_line": 1,
  "end_line": 1,
  "section_markdown": "...",
  "tracker_status": {"ok": true}
}
```

The `section_markdown` must be literal source text. No LLM-generated interpretation, semantic shelf choice, or inferred related section is permitted in this first slice. A PP retained/folded appendix remains a separately addressable source section unless later explicitly modeled and reviewed.

## Failure and preservation behavior

- Unknown/missing PP heading: clear nonzero CLI result; no partial/synthetic packet.
- Missing/unreadable Master Plan: clear nonzero CLI result.
- Tracker unavailable: preserve and return the exact source section, but label the tracker state/error explicitly rather than hiding it.
- Read-only: do not write any Plan Vault source/index/cache, do not enqueue work, and do not activate a worker or model call.

## Strict TDD acceptance

Write tests first and run each new test red before implementation. Add focused coverage for:

1. exact section boundary extraction (does not include the following heading);
2. stable heading, inclusive line range, source path, and SHA-256 provenance;
3. machine JSON shape and literal source section;
4. live status integration and graceful tracker-failure visibility;
5. unknown PP and missing-plan failures;
6. read-only behavior; and
7. CLI dispatch/help for `brief`.

Run focused tests and the full relevant plan test suite after implementation. Report exact commands/results and leave #1440 open for Tigwa’s independent review.

## Context and research alignment

- The reviewed Master Plan currently requires a full Plan read in `CLAUDE.md`, while the Plan is 1,759 lines / 114,601 bytes. This command is intended to make most task entry bounded and source-linked.
- PP-CODEGRAPH-001 is folded into PP-KNOWLEDGE-001. The existing a1131-directed eventual stack is Tree-sitter/FalkorDB graph + invariant catalog/Z3 + DuckDB traces + unified MCP. Do not re-scope, install, or configure this stack here.
- Recoll is discovery/recovery support, not authority. A source-derived packet must not depend on fuzzy search or a potentially stale external index.
- Official DeepSeek thinking-mode documentation confirms `thinking` defaults enabled, uses `extra_body: {"thinking":{"type":"enabled|disabled"}}`, and supports only `reasoning_effort: high|max` in thinking mode. Its common sampling controls have no effect in thinking mode. No DeepSeek/Aider/Hermes configuration changes are in scope for this coding task.

## Deliverables

1. Tested code and tests in the repository.
2. A concise Claude implementation report in `docs/TGW-Plan-Vault/inbox/tigwa/` naming changed files, test results, and an example redacted/no-sensitive-data packet.
3. Keep #1440 open pending independent review.
