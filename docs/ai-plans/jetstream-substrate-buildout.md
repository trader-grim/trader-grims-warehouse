# jetstream-substrate-buildout: sequence + scope the full JetStream cluster (audit, handoff, mailbox, LOADTEMP)

**Status:** Draft — 2026-07-22
**PP ref:** PP-AIOPS-001 (umbrella), PP-RUNNERCOMMS-001, PP-LOADTEMP-001
**Authorized by:** Dave, 2026-07-22 — "she's basically saying what I said, I
only used one sentence. Build it all... plan first."

## Problem / motivation

Three separate threads (mutation-audit stream, agent-handoff, mailbox
reliability) converged onto one shared substrate — NATS JetStream — this
planning week. Dave's framing of the mailbox piece ("basically email":
delivery guarantee, thread/reply-trail, versioned drafts, per-actor
compartmentalization) matches what Tigwa independently converged on from
the canonical PP-AIOPS-001 decisions, just from the opposite direction —
confirmation, not coincidence, per the design-reconvergence pattern
already on record for this project.

The broker itself is standing but not yet trustworthy: three live bugs
block it (dual-authority stream provisioning, a broken health probe, and
no acceptance evidence that compartmentalization/durability actually
hold), and two downstream packets (mailbox, LOADTEMP) are designed but
gated behind that trust being established. This plan sequences all of it
into dispatchable packets, respecting the blockers Tigwa named rather than
building mailbox/LOADTEMP on an unproven broker.

## Constraints (from settled architecture + this cluster's own decisions)

- **Don't re-open settled PP-AIOPS-001 decisions**: broker host = tgw-prod
  only, native NixOS package (no Docker), uniform 90-day/10GB retention
  split across streams. This plan only sequences work, it does not
  re-derive those.
- **Mixed packets split by boundary, always** (invariant E11/E12): any
  step touching `~/tgw-flake` routes to `nix-flake-maintainer`
  (commit → dry-activate → `tgw flake request-push`/`request-switch`,
  human executes); any step touching `src/tgw/`/`tests/` routes to
  `tgw-coder` in its own worktree+branch. No single dispatch crosses both.
- **`queue_jobs`/Postgres stays the work-state authority.** JetStream is
  transport/replay/audit, never a second work-state database. Mailbox
  redesign must not become an unscoped shared-SQL read surface either.
- **E14 (write-once/append-only evidence)** — mailbox draft revisions are
  the second real consumer of this pattern after agent-trace evidence;
  reuse the shape, don't re-derive it from scratch.
- **Compartmentalization is load-bearing** (Dave, 2026-07-22: "literally
  the value of the manual read-your-inbox dance") — must be enforced at
  the NATS account/subject-permission layer, not an app-level filter.
- **Fence discipline** — LOADTEMP's reading may include quota/token
  status; raw credentials never ride along, only derived headroom values.
- **"Tests pass" is not done"** — every step below has a live-verification
  requirement, not a unit-test-only bar, per Prime Directive 4 and
  Tigwa's explicit "started ≠ accepted healthy" distinction.

## Proposed approach — five packets, strict sequence

Sequence is a hard dependency chain, not a preference ordering — Tigwa
named #1638 and the acceptance suite as blockers, so nothing downstream
starts before they pass.

### Packet A — todo #1638: single-authority NATS stream provisioning
**Already fully spec'd**, `plan/packets/1638-nats-stream-single-authority.md`
— no new planning needed, this plan just places it first. Mixed packet:
flake side (`nats.nix` gets a declarative `QUEUE_TRANSITIONS` block) to
`nix-flake-maintainer`; app side (`nats_client.py`'s `_ensure_streams()`
becomes read-only) to `tgw-coder`. Dispatch as two separate packets per
the packet's own instructions. **Blocks everything below.**

### Packet B — todo #1639: fix `tgw_health`'s NATS check
**Root cause confirmed this session** (not yet in the packet form): both
`nats_client.check_nats()` and `nats_client.query_mutations()`
(`src/tgw/apis/nats_client.py:261-347`) call bare `asyncio.run(...)`,
which raises `RuntimeError: asyncio.run() cannot be called from a running
event loop` whenever the caller is already inside one — confirmed live
path: `mcp_server.py`'s `tgw_health()` tool (`@mcp.tool()`, FastMCP's
async runtime) calls `health.check_all()` synchronously
(`mcp_server.py:254-266`), which calls `health.check_nats()`
(`health.py:544`), which calls `nats_client.check_nats()` — three sync
frames deep inside an already-running loop. The plain CLI path
(`tgw health`) never hits this because nothing already has a loop
running there, which is why it went unnoticed until Tigwa exercised the
MCP path specifically.

**Fix:** both functions need to work whether or not a loop is already
running. Simplest robust option — spin the probe/query onto its own
short-lived thread with its own fresh event loop unconditionally
(mirrors the pattern `nats_client.py` already uses for the background
publisher thread, just one-shot instead of long-running), rather than
trying to detect-and-branch on `asyncio.get_running_loop()` at each call
site. Single dispatch, `tgw-coder`, `src/tgw/apis/nats_client.py` only —
no schema/data risk, no cross-boundary split needed.

**Acceptance:** call `tgw_health` via the MCP path (not just the CLI) and
confirm no `RuntimeError`; confirm the CLI path still works unchanged;
confirm a real probe result (`ok`/`streams`) is returned from both call
paths against the live broker.

### Packet C — JetStream acceptance-evidence suite
Not a code change — a verification packet, run against the post-A/B
broker before C's own gate is considered cleared. Tigwa's exact bar
(session-wrap facility cross-check, 2026-07-22):

1. **Cross-host connect/inspect** — independently connect and run
   `nats stream info` from both tgw-prod and a1131 against the one
   broker; confirm identical stream state from both.
2. **Durable publish + consumer ack** — publish a real message, consume
   it with a durable consumer, confirm the ack is recorded (not just
   that `publish()` returned without error — that's already covered by
   packet 1638's own step 4, this goes one step further to prove
   consumption).
3. **Denied cross-actor access** — attempt a read/write from an account
   without permission on another actor's subject namespace, confirm it
   is refused by the broker, not merely unused by convention. This is
   the one piece of infrastructure this whole plan doesn't have yet:
   NATS accounts/subject permissions per actor (Claude/Tigwa/future
   specialists) don't exist on the broker today — everything currently
   connects with one shared credential. **This sub-step needs its own
   small design/build step before it can be tested** — see Open
   Questions below.
4. **Restart/replay** — restart `nats-server`, confirm streams and
   already-published messages survive (JetStream file storage, expected
   to just work, but not yet proven live on this host).
5. **Repaired health check** — packet B, folded in as the final
   acceptance signal: `tgw_health` reports real broker state via both
   CLI and MCP paths.

Dispatch as one `nix-flake-maintainer` verification pass (steps 1, 4 —
host/service level) plus one `tgw-coder` pass (steps 2, 3, 5 — client
code level, may need a small test script under `tests/` or a throwaway
verification script per the one-off-scripts-announce convention). Once
per-actor NATS accounts exist (see Open Questions), step 3 needs its own
packet first.

### Packet D — PP-RUNNERCOMMS-001 mailbox-reliability packet
**Gated on C passing.** Builds the "basically email" mailbox on the
now-trusted broker:

- Subjects: `tgw.mailbox.<actor>.inbox` per actor (matches the
  already-decided naming convention in PP-AIOPS-001's convergence note).
- Delivery guarantee: a "send" isn't done until the broker acks it —
  matches Dave's framing directly, this is JetStream's native property,
  not new code.
- Reply trail: NATS reply-to/correlation subjects, no hand-rolled
  `parent_message_id` FK.
- Versioned drafts: append-only by construction — a draft's revision
  history is successive messages on the same subject, never an in-place
  edit. Same shape as E14, second real consumer of that pattern.
- Compartmentalization: rides the per-actor NATS accounts built for
  Packet C step 3 — same mechanism, not a second implementation.
- `inbox/<actor>/` markdown stays a synced export/record only, never
  the authoritative copy or proof of delivery.

**Acceptance criteria (Tigwa's list, verbatim, must all be true before
this is "done," not just "built"):**
- Broker acceptance is a distinct, separately-observable state from
  recipient delivery, consumer acknowledgement, and read state — all
  four are queryable independently, not collapsed into one status.
- Every attachment/draft revision carries: content hash, message/revision
  identity, parent/correlation identity, intended recipient. No silent
  export divergence between the broker's copy and the markdown export.
- Unavailable/stale consumer or export state surfaces as an
  operator-visible integrity exception — never silently treated as
  current.
- **Regression test: EBAY-DS-1077 replayed against the new mechanism** —
  no in-place draft overwrite, no unproved delivery claim, a stale
  replica must be caught, not silently served.

Not yet packet-sized in detail — this plan defers the actual
`docs/ai-plans/` or `packets/` write for Packet D to a follow-up pass
once C has actually passed (writing D's exact file-level spec now, before
knowing what the per-actor-account mechanism from C looks like, risks
re-deriving it wrong).

### Packet E — PP-LOADTEMP-001 packet
**Gated on its own two design gaps being closed, independent of C/D** (no
JetStream dependency — this PP is explicitly polled/Postgres, not an
event stream, per its own architecture decision):

1. **Per-host availability/failure-mode design.** Needs an explicit
   answer, not yet decided: what a worker does when its host can't reach
   the shared Postgres reading — must fail toward "assume hot" (block/
   slow down), never "assume cool" (proceed as if load were fine). Needs
   a stamped reading + max-age + the actual degraded-state code path
   named before this is packet-ready.
2. **Fence design.** Needs an explicit allowlist: which derived fields
   (quota/API headroom) are safe to publish through the reading vs. which
   raw collected data (anything token/credential-shaped) must stay local
   only. Not yet enumerated field-by-field.

Both gaps are design questions for a dedicated pass, not implementation
— this plan doesn't schedule Packet E's actual build dispatch yet,
only names it as the fifth item in sequence once its own two questions
have answers.

## Files to change

| File | Change |
|------|--------|
| `~/tgw-flake/nix/nats.nix` | Packet A — add declarative `QUEUE_TRANSITIONS` stream block |
| `src/tgw/apis/nats_client.py` | Packet A — `_ensure_streams()` becomes read-only; Packet B — fix `asyncio.run()` nesting in `check_nats()`/`query_mutations()` |
| *(new, TBD)* NATS account/permission config | Packet C step 3 — per-actor accounts, exact location depends on whether this lives in `nats.nix` (likely) or a separate auth config file |
| `docs/TGW-Plan-Vault/plan/packets/*.md` | New packets for B, C (accounts sub-step), D once sized |

## Acceptance criteria

- [ ] Packet A: `nats stream info QUEUE_TRANSITIONS --json` shows a
      bounded `max_bytes`, not `-1`; worker restart shows no `add_stream`
      call in logs.
- [ ] Packet B: `tgw_health` via MCP path returns a real result with no
      `RuntimeError`; CLI path unchanged.
- [ ] Packet C: all 5 sub-checks pass live, including a real denied
      cross-actor access attempt (not a code-review assertion that it
      would be denied).
- [ ] Packet D: EBAY-DS-1077 regression scenario replayed and passes
      against the new mailbox mechanism.
- [ ] Packet E: both design gaps have a written answer Dave has approved
      before any code is dispatched.

## Packet C technical deep-dive (2026-07-22, per Dave's "unfold" direction)

**Critical live finding, checked directly before designing this**: the
broker today has **zero authentication** — no `authorization` block in
`nats.nix` at all, confirmed live (`nats pub` from a bare unauthenticated
client succeeded against `127.0.0.1:4222`). This is contained only
because the broker is bound to localhost. **This sets a hard sequencing
constraint not previously written down: the per-actor account/permission
work below MUST land in the same change as the Tailscale bind, never
split across two separate dispatches with the bind landing first** — a
bind-then-auth order would create a real window where the broker is
reachable from the whole Tailscale network with no authentication at all.

### Per-actor subject-permission design (the "one account, per-actor
permissions" decision, made concrete)

NATS's `authorization` block (not full multi-tenant `accounts`, matching
the already-decided simpler model) supports per-user `permissions` within
one account — this is a direct, native fit, no custom mechanism needed:

```json
"authorization": {
  "users": [
    { "user": "claude", "password": "$2a$11$<bcrypt-hash>",
      "permissions": {
        "publish":   {"allow": ["tgw.mailbox.claude.>", "itemdata.>"]},
        "subscribe": {"allow": ["tgw.mailbox.claude.>", "itemdata.>", "queue.>", "$JS.API.>"]}
      }},
    { "user": "tigwa", "password": "$2a$11$<bcrypt-hash>",
      "permissions": {
        "publish":   {"allow": ["tgw.mailbox.tigwa.>", "itemdata.>"]},
        "subscribe": {"allow": ["tgw.mailbox.tigwa.>", "itemdata.>", "queue.>", "$JS.API.>"]}
      }},
    { "user": "tgw_worker", "password": "$2a$11$<bcrypt-hash>",
      "permissions": {
        "publish":   {"allow": ["itemdata.>", "queue.>"]},
        "subscribe": {"allow": ["itemdata.>", "queue.>", "$JS.API.>"]}
      }}
  ]
}
```

**Credential custody, per the existing single-facility rule** (CLAUDE.md:
"secrets from `secrets_root`, no hardcoded paths in `src/`"): the
plaintext NATS passwords live in `secrets_root/tgw.env`
(`NATS_PASSWORD_CLAUDE`, `NATS_PASSWORD_TIGWA`, `NATS_PASSWORD_WORKER`),
read via `tgw.apis.secrets.get_api_key()` on the client side — same
pattern as every other provider key. **Only the bcrypt hashes go into
`nats.nix`** (git-committed, effectively public within the repo) — this
is exactly why NATS supports bcrypt config-side in the first place, not a
TGW-specific workaround. `nats-server --bcrypt` (or `natscli`'s bcrypt
helper) generates the hash from the plaintext once; the plaintext itself
never touches the flake.

`tgw_worker` is a genuinely new credential — today's workers connect with
no credential at all (matches the zero-auth finding above). Every worker
process picks up `NATS_PASSWORD_WORKER` the same way it already picks up
other secrets.

### The Tailscale bind

```nix
services.nats.settings.host = "100.x.x.x";  # tgw-prod's Tailscale IP, exact value TBD at dispatch time
```

Straightforward once auth lands — the constraint above is entirely about
*sequencing*, not the bind's own mechanics. `nats.nix`'s existing header
comment ("no cross-host NATS traffic is part of this design") is now
stale relative to the 2026-07-22 Tailscale-bind decision — gets corrected
in the same dispatch, not left contradicting the live config.

### Acceptance-suite test procedures (concrete, per sub-check)

1. **Cross-host connect/inspect** — from a1131:
   `nats --server nats://claude:$NATS_PASSWORD_CLAUDE@<tailscale-ip>:4222 stream info ITEMDATA_MUTATIONS --json`
   — compare byte-for-byte against the same command run on tgw-prod.
2. **Durable publish + consumer ack** — `nats consumer add ITEMDATA_MUTATIONS test-c2 --pull`,
   publish one throwaway message, `nats consumer next ITEMDATA_MUTATIONS test-c2`,
   confirm `num_ack_pending` drops to 0 after ack, not just that publish returned success.
3. **Denied cross-actor access** — connect as `tigwa`, attempt
   `nats pub tgw.mailbox.claude.inbox.test "should be denied" --server nats://tigwa:$NATS_PASSWORD_TIGWA@<host>:4222`,
   confirm the broker itself returns a permissions-violation error (visible
   in `nats-server`'s own log as an `authorization violation`, not merely
   "the app never sends this by convention").
4. **Restart/replay** — `systemctl restart nats.service`, re-run check 1;
   confirm stream message count unchanged (JetStream file storage should
   just work, but per Prime Directive 4 this needs to be observed live on
   this actual host, not assumed from NATS's general documentation).
5. **Repaired health check** — `tgw_health` via both CLI and MCP paths
   (todo #1639's fix, already landed) reports real broker state.

Every throwaway artifact (`test-c2` consumer, `TEST_UNITBYTES`-style
streams if any) gets deleted after — same discipline the earlier
retention-fix investigation already used, not new.

### Dispatch shape

Two packets, matching the mixed-boundary rule (invariant E11/E12):
- **`nix-flake-maintainer`**: `nats.nix` — add the `authorization` block
  (bcrypt hashes only) + Tailscale bind, in one commit (never split per
  the sequencing constraint above). Local commit + dry-activate +
  `tgw flake request-push`/`request-switch`, human executes.
- **`tgw-coder`**: issue the four `NATS_PASSWORD_*` secrets into
  `secrets_root/tgw.env`, update any client code that connects without
  credentials today (`nats_client.py` and worker connection setup) to
  read and pass them, write the acceptance-suite test script (checks 1-5
  above) under `tests/` or as an announced one-off script.

**No-go condition**: if the flake dispatch lands the Tailscale bind
before the `nix-flake-maintainer` step confirms the `authorization` block
is active in the same `nixos-rebuild switch`, treat it as a live incident
per Prime Directive 2 — an unauthenticated broker reachable from Tailscale
is exactly the kind of exposure this project's own security findings
(PP-COHESION-001's Syncthing/NFS exposure items) already treat as
serious.

## Open questions

- **Per-actor NATS accounts don't exist yet** — Packet C step 3 and all
  of Packet D's compartmentalization depend on this existing, but it's
  not scoped as its own packet in this plan. Recommend: a short design
  pass (not a full packet) deciding account-per-actor vs.
  subject-permission-per-shared-account before Packet C is dispatched,
  since the shape of that decision changes what step 3's test actually
  looks like.
- **Packet D's exact file-level spec is deliberately deferred** (see
  above) — writing it now risks assuming an account mechanism that
  Packet C's design pass might land differently.
- **Packet E's build dispatch is not scheduled** — only unblocked once
  Dave approves answers to its two design gaps; this plan doesn't presume
  those answers.
- Should Packets B and C's account sub-step be combined into one
  `tgw-coder` dispatch (both touch `nats_client.py`/broker-adjacent test
  code) or kept separate? Leaning separate — B is a pure bugfix with its
  own clean acceptance test, C's account work is new infrastructure;
  bundling risks a partial-revert problem if one fails review and not
  the other.
