# Governed tgw-review provider — v1 (2026-08-16)

This runbook restores the established interactive independent-review route
without making `tgw-review` a Claude-owned capability and without requiring
Qualified Execution Service (QES). QES remains an optional, separately
admitted execution provider.

## Authority and historical provenance

- Canonical skill: `agent-services/skills/tgw-review/SKILL.md`.
- Output contract: `tgw-code-review/v1`.
- Promptcraft card/handoff contract: `tgw-execution-card/v1` and
  `tgw-launcher-handoff/v1`.
- Established restoration: source commits `234ff848`, `16e4d850`, and
  `0925a657`; retained successful-review history includes `5b86421d`,
  `048902b2`, and `18e0677a`.
- Current Plan authority for this restoration:
  `f0a8cf22b2c7b2f064292a048ffcb8ee98919e99`.

The governed adapter is provider-neutral. The packet's selected provider,
Promptcraft receiver identity, provider identity record, execution record,
governed role receipt, and pinned X-store bundle must all name the same
provider. Admission never branches on the string `claude`.

## Current selected implementation

The currently proven implementation is the `claude` account on tgw-lib:

- Claude Code 2.1.223 at `/home/claude/.local/bin/claude` (resolve and capture
  the exact non-symlink target before use);
- canonical `tgw-review` skill installed at
  `/home/claude/.claude/skills/tgw-review`;
- `tgw` and `tgw-context` MCP servers in `/home/claude/.claude.json`;
- current first-party Claude authentication; and
- receiver identity `claude:tgw-review`.

Never retain email addresses, OAuth material, tokens, or the full auth file in
review evidence. Retain a non-secret account-identity hash, provider/version,
exact configured-command link identity, resolved executable/skill/MCP hashes,
and the authentication health boolean.

The 2026-08-16 observation recorded the current provider implementation as:

- configured command `/home/claude/.local/bin/claude`, a symlink to
  `/home/claude/.local/share/claude/versions/2.1.223` (uid/gid 1006, mode
  0777, one link); and
- resolved target version 2.1.223, uid/gid 1006, mode 0755, one link, size
  290728968, SHA-256
  `98226474f802e3094d6a86c5ade8883c16206d0fcb5c400b7401c800063e99d7`.

These values are provider evidence, not constants in the admission protocol.
A fresh execution must recapture them and HOLD if either named/link identity or
the held target differs before, during, or after use.

## Required inputs

Before launch, the controller must create and freeze:

1. a root-owned, non-writable, single-link source snapshot for exact candidate
   commit/tree X;
2. Plan f0, solution/closure, CodeGraph, execution environment, candidate
   evidence, and receipt-sink bindings;
3. an unexpired independent-review execution card and Promptcraft handoff;
4. the provider's exact executable, version, account identity, canonical skill,
   MCP configuration identity, closed environment, and allowed ownership; and
5. an X-store publisher bound by the card's `receipt_sink` binding.

The adapter holds the source root and executable/skill/MCP artifacts through
use, executes the held executable via `/proc/self/fd`, bounds time and combined
output, terminates and proves the process group empty, rechecks named and held
identities, validates exactly one `tgw-code-review/v1` result, and requires the
bound X-store publisher to acknowledge the execution hash.

For the current Claude implementation the admitted provider command is the
historical command, expressed with exact adapter framing:

```text
<resolved-claude-executable> -p {prompt} --model opus --effort high \
  --max-turns 35 --tools Read,Bash,Glob,Grep \
  --permission-mode dontAsk --setting-sources '' \
  --add-dir {snapshot} --output-format json --no-session-persistence
```

`{prompt}` and `{snapshot}` must each occur exactly once. The adapter replaces
the executable and snapshot paths with held `/proc/self/fd` identities.

## Evidence and admission

The provider emits `tgw-governed-review-execution/v1`. It binds the exact
card/handoff/Promptcraft receipt, Plan, source commit/tree/snapshot,
CodeGraph/environment/resource bindings, provider identity, command policy,
bounded lifecycle, output hashes, and semantic result. The execution record is
published by the card-bound sink before it is returned.

`tgw-integrated-candidate-review-result/v2` accepts exactly one execution
binding:

- legacy/optional `qualified_execution_proof_hash`; or
- provider-neutral `governed_review_execution_hash`.

The pinned independent-review bundle retains the packet, integrated report,
result, governed execution, card, handoff, and Promptcraft receipt. Admission
rehashes all objects, verifies the Promptcraft lease at the recorded start
time, cross-binds provider/source/Plan/snapshot, requires a PASS result, and
requires the independent governed receipt to name the retained execution.

## Installation status

This source change is a candidate only. It is not installed or deployed.
Before first production use, the release operator must install the reviewed
commit, provision a root-owned snapshot staging directory and X-store
publisher, capture a fresh provider identity, run the focused tests below, and
perform one real Claude review with secrets excluded from its retained
evidence.

```bash
PYTHONPATH=src pytest -q \
  tests/test_governed_review_adapter.py \
  tests/test_candidate_review.py \
  tests/test_candidate_receipt_sink.py
```
