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
- provider-neutral protected skill, loopback context-service MCP config, and runtime
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
   generic-skill projection and source-provenance receipt, held loopback-MCP
   config, minimal runtime closure, closed
   environment, fresh authentication-health evidence, and per-artifact owner
   policy;
5. a registered, separately privileged context-broker service and public key
   capable of issuing a signed card/handoff/resource-retrieval attestation;
   the separately operated loopback context service alone receives one pre-bound broker request
   credential, the controller receives a different readback credential, and
   both signing authorities remain unavailable to the provider; and
6. a protected X-store descriptor whose exact reference and descriptor hash
   equal the card's `receipt_sink` binding, plus an exact execution-environment
   record stating that provider networking is the shared host network.

The adapter holds the root-owned request, source root, sandbox, runtime,
executable, skill, MCP config, and credential descriptors
through use. It launches a minimal-root bubblewrap user/PID namespace as the
exact selected provider uid/gid (never root for the current Claude profile), executes the
held executable via `/proc/self/fd`, installs the exact protected skill into an
ephemeral HOME, and passes the held MCP config with strict settings. Time and
combined output are bounded; PID-namespace teardown proves descendants cannot
escape. Named and held identities are rechecked, exactly one
`tgw-code-review/v1` result is accepted, and the card-bound X-store must return
an exact pinned readback.

Before launch, the controller issues one root-held context grant containing a
fresh 256-bit challenge, exact broker request hash, `issued_at`, `not_before`,
and `expires_at`. Its entire lifetime may not exceed 15 minutes. The same exact
request is installed as the broker's one-use grant; neither the challenge nor a
test callback is generated after launch. The challenge and non-secret grant are
disclosed in the governed prompt. A passing provider must use Skill and the nonempty exact MCP
tool policy and call `tgw_context_bundle` with that challenge and exact skill
contract hash. The separately operated context service uses a request credential bound to
that exact client/challenge/card/handoff/skill/resource receipt and resource
map; the privileged broker consumes it before retrieval. The broker retains the
complete signed service attestation and fetched resource bundle under the exact
client and challenge. No provider-output field or provider-writable file is
trusted for this evidence. After the provider exits, the adapter independently
consumes the sole matching bundle from the registered context broker with a
distinct client-bound controller credential. A missing, duplicate, expired, or
already-consumed challenge is a HOLD. The broker response includes the exact fetched bytes;
the review-visible MCP result is decoded from those bytes, never from local
Plan/source discovery.
The signed retrieval attestation must
bind that challenge and exact uid/gid, the exact card/handoff/resource receipt,
and every card resource. The returned Plan,
source, CodeGraph, and environment references/hashes are compared with the
card, while every returned byte payload is independently rehashed by both the
MCP service and controller. This evidence is named registered-resource
retrieval; it does not claim that MCP invocation itself is cryptographically
exclusive. A report alone, or a provider echo of mounted hashes, is not
retrieval evidence.

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
identities and maps the held source snapshot read-only. The held MCP config may
name only the exact admitted loopback context-service SSE endpoint and contains
no broker request or readback credential. Its
bound Plan, source, CodeGraph, and environment bindings must exactly equal the
retained card.

The sandbox intentionally shares the host network because the selected model
provider and admitted MCP route require it. This is not networkless isolation,
and it is not endpoint confinement. The provider identity records the observed
model and context-service endpoints for exact environment evidence, with
`mode=shared-host-network` and `endpoint_confinement=false`; it makes no claim
that Bubblewrap enforces an allow-list. Per-cgroup or proxy confinement belongs
to the separately planned PP-AIOPS network effort and is not a W08 admission
dependency. Model endpoints remain HTTPS. The governed
context service is exact loopback HTTP SSE, and the separately controlled
context service reaches the broker over exact loopback HTTP. Both shipped
servers reject non-loopback binds; no unimplemented TLS proxy is claimed. The
context-service endpoint is admitted;
the privileged broker endpoint is explicitly forbidden from the provider
namespace. A self-hash or caller assertion is a HOLD.

## Evidence and admission

The provider emits `tgw-governed-review-execution/v1`. It binds the exact
card/handoff, distinct execution-resource and Promptcraft receipts, Plan,
source commit/tree/snapshot,
CodeGraph/environment/resource bindings, provider identity, command policy,
the full non-secret preissued context grant, bounded lifecycle, output hashes,
fully validated semantic result, and signed
registered-resource retrieval with exact resource-bundle hash. The execution
record is published by the card-bound sink
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
governed role receipt and candidate governed-execution receipt, publishes the
five mandatory governed-execution artifacts and exact
`candidate:<commit>:governed-execution:independent-review` v2 bundle, then
publishes the packet/report/result and all seven semantic-review artifacts in
the v4 pointer bundle. It reads every object back from X before returning its
final result.

## Installation status

This source change is a candidate only. It is not installed or deployed. The
current selected provider is HOLD, not disabled: its executable and existing
credential are present, but the protected generic skill, loopback MCP config,
minimal runtime projections, separately privileged context broker
and its protected backend credential/signing authority, registered signed
context readback, X publisher, and protected execution-environment authority
have not been issued. Before first
production use, the release operator must install a reviewed successor,
provision those root-owned projections plus snapshot staging and the X-store,
capture a fresh provider identity/health receipt, bind the truthful shared-host
network environment, run the focused tests, then perform one real minimal-root bubblewrap
review smoke and one real candidate review with secrets excluded from retained
evidence. Do not ask the operator to authenticate again unless the fresh
provider health check itself fails.

The installed fixed entry point is:

```bash
tgw-governed-review --request /run/tgw-review/root-owned-request.json
```

The separately protected loopback broker daemon is started with:

```bash
tgw-governed-review-context-broker \
  --config /run/tgw-review/root-owned-broker-config.json \
  --host 127.0.0.1 --port 8788
```

Its root-owned config carries only environment-variable names for secrets and
one exact, fresh, time-bounded request grant. Startup derives the broker public
key from the loaded private key and requires exact equality with the configured
key. The same protected config embeds the exact qualified resource-service
catalog and its hash. Startup requires the backend descriptor, service/client,
capabilities, key ID, and Ed25519 public key to equal that catalog entry, then
requires every preissued grant to carry the same card-bound catalog ref/hash,
before backend health or server/grant arming. It also requires unique, disjoint
request/readback credentials with exact client coverage. The request credential is consumed once; abandoned
bundles expire, and the exact client-bound readback is also consumed once.
The governed MCP daemon is separately started with
`--governed-review-sse`; it binds only loopback and exposes only the strict
governed `tgw_context_bundle` surface. The ordinary context daemon is not an
admitted provider endpoint.

The request must be a bounded, root-owned, non-writable, single-link regular
file. The entry point holds and rechecks that exact file through provider
execution and pinned X-store publication; ad hoc argv composition is not an
operator interface.

```bash
PYTHONPATH=src pytest -q \
  tests/test_governed_review_adapter.py \
  tests/test_governed_review_context_broker.py \
  tests/test_candidate_review.py \
  tests/test_candidate_receipt_sink.py
```
