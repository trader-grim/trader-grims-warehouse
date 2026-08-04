# Submission — retrieval-first MCP Plan Brief v1

**Submitted by:** Tigwa
**Program:** PP-KNOWLEDGE-001
**Tracker:** #1439 (open pending review/trial)
**Date:** 2026-07-18

## Goal

Reduce repeated agent context burden while improving task results: retrieve the smallest authoritative Plan Vault slice needed for a scoped task, with enough provenance to verify it, rather than repeatedly loading the entire growing Master Plan.

The Master Plan remains canonical. This submission does not create a summary authority, generated cache, worker, Recoll dependency, or startup-contract change.

## Current implemented vertical slice

`src/tgw/mcp_server.py` now registers the read-only MCP tool:

```text
tgw_get_plan_brief(pp: str)
```

For an exact PP identifier it returns:

- canonical Master Plan path, SHA-256, byte count, and mtime at read time;
- the exact selected heading and line/byte ranges;
- SHA-256 and literal Markdown for that exact section;
- explicit error codes for invalid identifier, unavailable plan, missing PP, ambiguity, and overlarge section;
- a bounded 64 KiB section cap with no partial section returned;
- expected linked `plan/pp/<PP>.md` presence/absence and provenance;
- warnings that require full-plan reading for broad planning, reconciliation, audit, ambiguity, or Dave-directed review.

PP matching is deliberately start-of-heading only. A different heading that merely cross-references or says it was folded into the requested PP is not allowed to steal or make ambiguous the requested PP’s canonical section.

## Evidence

- Tests: `64 passed` across `tests/test_plan_render.py` and `tests/test_mcp_server.py`.
- `tests/test_mcp_server.py` includes a regression for the folded/cross-reference false-ambiguity case.
- Fresh actual client path tested: a1131 → SSH stdio → tgw-prod `tgw.mcp_server`.
  - 11 tools discovered.
  - `tgw_get_plan_brief(PP-KNOWLEDGE-001)` returned `ok=true`.
  - Current canonical Master Plan SHA-256:
    `8c0550e86ab95ee6c0f7411b94773891bdec587d6d97c325f618cf58699861a8`.
  - Selected PP-KNOWLEDGE-001 section: lines 1026–1216.
  - No fabricated PP detail: `plan/pp/PP-KNOWLEDGE-001.md` does not currently exist and is reported absent.
- `git diff --check` passed for the submitted MCP changes.
- Hermes gateway restarted; `hermes mcp test tgw` discovers the 11th tool.

## Required Claude review and conditional follow-up implementation

Please test/review the current v1 behavior and, **if you accept it**, implement this focused refinement before considering the packet contract settled:

1. Move the deterministic parser/retrieval logic from `src/tgw/mcp_server.py` into a shared pure helper in `src/tgw/plan_render.py` (for example `plan_brief(cfg, pp_ref)`).
2. Make the MCP tool delegate to that helper; do not retain two parsers or hard-code the Plan Vault root in MCP.
3. Derive paths from existing `cfg['plan_master_path']` and `cfg['plan_vault_path']`.
4. Keep linked PP documents metadata-only in this packet (path/status/hash/size); do not inline arbitrary detail documents, even if they happen to fit the cap. A future explicit detail retrieval tool can have its own bound/provenance contract.
5. Add test-first coverage in `tests/test_plan_render.py` for exact section boundaries, lowercase normalization, cross-reference headings, source/section hashes, unavailable/missing/ambiguous/oversize errors, and no writes.
6. Retain/add FastMCP-boundary coverage in `tests/test_mcp_server.py` using `tool.run({"pp": ...})`, not only a direct Python function call.
7. A later `tgw plan brief` CLI command may share the helper, but do not broaden this review into CLI/startup changes unless an existing approved task explicitly scopes that work.

## Non-goals and gates

- Do not alter `CLAUDE.md`, agent startup rules, workers, Recoll scheduling, Plan Vault text, queue state, SSH credentials, or eBay/catalog data.
- Do not treat a retrieval response as a completed task or a human decision.
- Before any startup-contract change, run representative tasks in parallel: current full-plan loading versus the packet, and report any authority gaps to Dave.

## Review questions

1. Does the v1 source/provenance contract preserve Master Plan authority correctly?
2. Does the proposed shared-helper refactor preserve one deterministic implementation for MCP and possible future CLI use?
3. What live status source, if any, should a later packet include without establishing stale state as a second truth?
4. What representative task set and acceptance criteria should govern the parallel startup trial?
