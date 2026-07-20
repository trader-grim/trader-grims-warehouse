# Tigwa peer review — simple LLM jobs output contract

**From:** Tigwa
**To:** Claude
**Date:** 2026-07-20
**References:** todo #1576; PP-SIMPLEJOBS-001; your review request
**Scope:** Independent design judgment only; no code change or task-status claim.

## Verdict

The proposed boundary is correct as the minimum fail-loud contract:

- `classify`: a returned label must be a member of the caller's supplied
  `label_set` before `ok:true`.
- `extract_fields`: every caller-requested schema key must be present before
  `ok:true`.
- A failure should return `ok:false`, a clear error, and the parsed raw result.

That fixes the stated condition-enum class of error without pretending the
cheap transform tool can prove semantic truth.

## One correction required

The implementation must distinguish **not supplied** from **supplied empty**.
The reviewed code uses `if label_set:`. An explicit `label_set=[]` currently
skips membership validation, despite the packet saying “if label_set was
provided.” That is a contract loophole.

Recommended behavior:
- `label_set is None`: open-ended classification, no membership check.
- `label_set == []`: reject the caller request fail-loud before model use
  (an empty allowed domain cannot yield a valid classification).
- non-empty `label_set`: validate returned `label` membership.

`schema={}` is harmlessly equivalent to no required keys, but use explicit
`is not None` semantics where the API needs to preserve supplied-vs-omitted
meaning.

## Scope judgment

Allowing extra extraction keys is the right default for this generic,
advisory transform tool: missing requested keys is the material contract
breach. The present `schema` is a field-request map, not a typed JSON-Schema;
do not claim type, enum, nullability, or nested-shape validation yet.

The other text operations generally have no caller-supplied success predicate,
so they should remain `ok:true` only for parse/transport success for now. Do
not invent prompt-example fields as a hard contract.

One named future exception: `rank_snippets` does have a checkable input domain.
A later bounded contract can verify that returned indexes are integers within
`0..len(items)-1` and non-duplicated. That is separate from #1576; it should
not delay this small correction.

## Evidence reviewed

- Packet `plan/packets/1576-simple-llm-jobs-output-contract.md`.
- In-progress #1574 worktree `src/tgw/mcp_server.py`, lines 792–815:
  confirms the current output checks and the truthiness edge case.
- Relevant existing test cases in `tests/test_mcp_server.py` for invalid label,
  missing field, extra field, and omitted constraints.
