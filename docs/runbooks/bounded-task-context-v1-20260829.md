# Bounded task context v1

This contract reconciles Todo 1919 into the Todo 1916 next-evolution DAG.  The
continual RLM harness owns its task cursor and evidence ledger.  Context MCP is
a read-only orientation/projection service, never Todo authority, dispatch,
admission, or an execution gate.

Implementation, troubleshooting, remediation, review, Doctor, and operator-
assistant roles use `tgw_context_todo_current` or `tgw_context_todo_exact`.  A failed exact lookup returns
`ABSENT`; it never invokes `tgw_get_todo(agent empty)` or any all-agent backlog
operation.  Open records outside the bound task are `OPEN_BUT_IRRELEVANT`.
Direct dependency bodies require `tgw_context_todo_dependencies` with each
direct Todo ID explicitly declared.  There is no implicit transitive expansion.

Administrative and planning inventory is a separate explicit
`tgw_context_todo_inventory` operation.  It requires a declared purpose and
limit, returns metadata-first summaries, and accounts for returned, omitted,
body-omitted, cursor, and truncation state.  It is not orientation and bounded
roles default-deny full-backlog bodies.

Every operation returns a `tgw-context-retrieval-evidence/v1` envelope for the
continual harness to append to its evidence ledger.  It binds operation, exact
scope, source generation, arguments, result hash, evidence head, freshness,
truncation, omission counts, and its own hash.  Outcomes remain distinct:
`CURRENT`, `STALE`, `MISMATCHED`, `ABSENT`, `OPEN_BUT_IRRELEVANT`, `TRUNCATED`.

Tool Selector may discover and rank these operations, but cannot replace an
exact operation with inventory, authorize a dependency, or invoke an effect.
Promptcraft handoffs carry execution-card resource references and hashes, not
copied Todo/backlog bodies.  Review retrieval is limited to its candidate-bound
card, Plan citations, CodeGraph, environment, acceptance, lease, receipt sink,
and relevant receipts.  Todo 1915 candidate
`0f6c9da37fb690d4c42afe7b96e273ff824cc7e8` is neither modified nor made
dependent on this later leaf.
