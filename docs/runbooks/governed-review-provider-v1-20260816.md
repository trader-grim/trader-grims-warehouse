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

## Current selected provider evidence

The currently proven implementation is the `claude` account on tgw-lib:

- Claude Code 2.1.223 at `/home/claude/.local/bin/claude` (resolve and capture
  the exact non-symlink target before use);
- discovered `tgw-review` skill link at
  `/home/claude/.claude/skills/tgw-review` (not an admitted execution input);
- provider-neutral protected skill, context-provider, MCP-config, and runtime
  projections supplied by the review controller;
- current first-party Claude authentication; and
- receiver identity `claude:tgw-review`.

Never retain email addresses, OAuth material, tokens, credential content, or a
credential hash in review evidence. The provider's existing credential is
mounted read-only at the provider's expected location inside an ephemeral
HOME. Evidence retains only its non-secret reference and held file metadata,
plus a non-secret account identity, fresh health receipt, provider/version,
and exact configured/resolved artifact identities.

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

The same observation found the discovered skill link to be a mode-0777
provider-owned symlink whose `SKILL.md` resolved to a codex:tgw-coders
mode-0664 development file. That tree is valid canonical source material but
is not an executable review input. Installation must atomically create a
root-owned, non-writable projection and retain a projection receipt binding
its canonical source manifest to the protected manifest. No reauthentication
is required for that operation.

## Required inputs

Before launch, the controller must create and freeze:

1. a root-owned, non-writable, single-link source snapshot for exact candidate
   commit/tree X;
2. Plan f0, solution/closure, CodeGraph, execution environment, candidate
   evidence, and receipt-sink bindings;
3. an unexpired independent-review execution card and Promptcraft handoff;
4. the provider's exact executable, version, account identity, root-protected
   generic-skill projection and source-provenance receipt, held MCP config,
   held executable context-provider closure, minimal runtime closure, closed
   environment, fresh authentication-health evidence, and per-artifact owner
   policy;
5. a registered context-bundle service and public key capable of issuing a
   signed card/handoff/resource-retrieval attestation; and
6. a protected X-store descriptor whose exact reference and descriptor hash
   equal the card's `receipt_sink` binding, plus a fresh signed receipt from
   the host egress controller proving the admitted endpoint policy is active.

The adapter holds the root-owned request, source root, sandbox, runtime,
context provider, executable, skill, MCP config, and credential descriptors
through use. It launches a minimal-root bubblewrap user/PID namespace as the
exact selected provider uid/gid (never root for the current Claude profile), executes the
held executable via `/proc/self/fd`, installs the exact protected skill into an
ephemeral HOME, and passes the held MCP config with strict settings. Time and
combined output are bounded; PID-namespace teardown proves descendants cannot
escape. Named and held identities are rechecked, exactly one
`tgw-code-review/v1` result is accepted, and the card-bound X-store must return
an exact pinned readback.

Each attempt creates a fresh 256-bit challenge and a bounded held receipt file
which is writable only by the selected sandbox uid. The challenge is disclosed in the
governed prompt. A passing provider must use Skill and the nonempty exact MCP
tool policy and call `tgw_context_bundle` with that challenge and exact skill
contract hash. The MCP writes its completed run identity, observed uid/gid,
card, skill, and attestation hashes to the held receipt file; no provider-output
field is trusted for this evidence. The adapter independently reads the run
back from the registered context service. The signed retrieval attestation must
bind that challenge and exact uid/gid, the exact card/handoff/resource receipt,
and every card resource. The returned Plan,
source, CodeGraph, and environment values are then compared byte-for-byte with
the card. A report alone, or a provider echo of mounted hashes, is not context
consumption evidence.

For the current provider implementation, the registry may select an argv such
as the following. The admission schema does not depend on this provider or
these flags; it binds the exact configured template and the generic tool and
network policies:

```text
<resolved-provider-executable> -p {prompt} --model opus --effort high \
  --max-turns 35 --tools Read,Glob,Grep,Skill,<exact-read-only-MCP-tools> \
  --disallowedTools Bash,Edit,Write,NotebookEdit \
  --permission-mode dontAsk --setting-sources '' \
  --mcp-config {mcp_config} --strict-mcp-config \
  --add-dir {snapshot} --output-format json --no-session-persistence
```

`{prompt}`, `{snapshot}`, and `{mcp_config}` must each occur exactly once. The
adapter replaces executable and config inputs with held `/proc/self/fd`
identities and maps the held source snapshot read-only. The MCP config may
name only executable files within the held context-provider manifest. Its
bound Plan, source, CodeGraph, and environment bindings must exactly equal the
retained card.

The sandbox intentionally shares the host network because the selected model
provider and admitted MCP route require it. This is not networkless isolation.
The provider identity binds a sorted exact HTTPS endpoint allow-list and its
hash. Bubblewrap does not enforce an endpoint allow-list, so admission also
requires a fresh Ed25519-signed `ENFORCED` receipt from the host egress
controller for that exact policy. A self-hash or caller assertion is a HOLD.

## Evidence and admission

The provider emits `tgw-governed-review-execution/v1`. It binds the exact
card/handoff/Promptcraft receipt, Plan, source commit/tree/snapshot,
CodeGraph/environment/resource bindings, provider identity, command policy,
bounded lifecycle, output hashes, fully validated semantic result, and signed
context consumption. The execution record is published by the card-bound sink
before it is returned.

`tgw-integrated-candidate-review-result/v2` accepts exactly one execution
binding:

- legacy/optional `qualified_execution_proof_hash`; or
- provider-neutral `governed_review_execution_hash`.

The pinned independent-review bundle retains the packet, integrated report,
result, governed execution, card, handoff, and Promptcraft receipt. Admission
rehashes all objects, verifies the Promptcraft lease at the recorded start
time, cross-binds provider/source/Plan/snapshot, requires a PASS result, and
requires the independent governed receipt to name the retained execution.
The fixed producer does not stop at an execution summary: it derives the
governed role receipt, packet/report/result, publishes all seven artifacts,
publishes the v4 pointer bundle, and reads every object back from X before
returning its final result.

## Installation status

This source change is a candidate only. It is not installed or deployed. The
current selected provider is HOLD, not disabled: its executable and existing
credential are present, but the protected generic skill, context-provider,
MCP-config, minimal runtime projections, registered signed context readback,
X publisher, and host egress enforcement have not been issued. Before first
production use, the release operator must install a reviewed successor,
provision those root-owned projections plus snapshot staging and the X-store,
capture a fresh provider identity/health receipt, prove the matching egress
policy, run the focused tests, then perform one real minimal-root bubblewrap
review smoke and one real candidate review with secrets excluded from retained
evidence. Do not ask the operator to authenticate again unless the fresh
provider health check itself fails.

The installed fixed entry point is:

```bash
tgw-governed-review --request /run/tgw-review/root-owned-request.json
```

The request must be a bounded, root-owned, non-writable, single-link regular
file. The entry point holds and rechecks that exact file through provider
execution and pinned X-store publication; ad hoc argv composition is not an
operator interface.

```bash
PYTHONPATH=src pytest -q \
  tests/test_governed_review_adapter.py \
  tests/test_candidate_review.py \
  tests/test_candidate_receipt_sink.py
```
