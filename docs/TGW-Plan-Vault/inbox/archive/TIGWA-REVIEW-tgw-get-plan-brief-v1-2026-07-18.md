# Review request — `tgw_get_plan_brief` v1

**From:** Tigwa
**PP:** PP-KNOWLEDGE-001 / todo #1439
**Purpose:** First deterministic retrieval-first MCP slice to reduce routine Master Plan context load without creating a second plan authority.

## Artifact under review

`src/tgw/mcp_server.py` now exposes read-only `tgw_get_plan_brief(pp)`.

For one exact PP identifier, it returns:

- canonical Master Plan path, SHA-256, size, and mtime;
- exact Master Plan heading plus line/byte anchors and section SHA-256;
- the exact source section, not a generated summary;
- an expected `plan/pp/<PP>.md` detail document when present and within the 64 KiB packet bound;
- explicit errors/warnings for invalid, missing, ambiguous, or overlarge requests.

Canonical lookup matches the PP identifier at the start of a heading only. A separate heading that merely says another PP was folded into the requested PP is not treated as a competing authority section.

## Evidence

- TDD regression tests: `25 passed` in `tests/test_mcp_server.py`.
- Fresh a1131 → SSH stdio → tgw-prod MCP client verification:
  - tool registered: true;
  - 11 tools discovered;
  - `tgw_get_plan_brief(PP-KNOWLEDGE-001)` returned `ok=true`;
  - canonical source SHA-256: `8c0550e86ab95ee6c0f7411b94773891bdec587d6d97c325f618cf58699861a8`;
  - matched the PP-KNOWLEDGE-001 section at lines 1026–1216.
- `git diff --check` passed for the two changed files.

## Requested review

1. Does this satisfy the retrieval-first authority/provenance boundary in `reference/TGW-Context-Burden-Retrieval-First-Review-2026-07-15.md`?
2. Is the start-of-heading matching rule correct for folded/retired PP cross-references?
3. What current task-status source should be linked into a later packet revision without making stale state a second truth?
4. Do not alter `CLAUDE.md` startup behavior yet. Recommend a bounded parallel trial and acceptance criteria first.

No business data, queue state, SSH credentials, plan text, or startup rules were changed by this implementation.
