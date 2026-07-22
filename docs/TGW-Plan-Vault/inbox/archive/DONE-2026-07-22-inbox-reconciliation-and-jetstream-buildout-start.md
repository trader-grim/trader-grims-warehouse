# DONE — 2026-07-22 continuation: inbox reconciliation + JetStream buildout kickoff

**Session arc:** started by clearing the 5-file inbox backlog (mostly
already folded into the master plan from an earlier turn this same
conversation, before context compaction — only Tigwa's two 2026-07-22
reconciliation notes were genuinely new). Dave then authorized "build it
all" and named new context: **planning for a month-long build sprint
funded by a Claude Max-plan upgrade** (Dave + Tigwa + incidental API
spend) — not just the JetStream cluster, everything currently scoped
across open PPs. Sent Tigwa a note flagging this, since her
PP-POSTGRES-001 sequencing call ("pipeline/UI first, defer Postgres")
was made without knowing this capacity was coming.

## What's actually done and live

- **Todo #1638** (dual-authority NATS stream bug) — DONE, both halves,
  live-verified:
  - Flake side: `nats.nix` now declares `QUEUE_TRANSITIONS` bounded
    (was unbounded `-1`, the original bug). **Took a 4th live failure
    on this file** to land — the first attempt (8GB/2GB split, summing
    exactly to the account's 10GB ceiling) passed `dry-activate` clean
    and still failed live with `insufficient storage (10047)` — NATS
    admission control rejects reserving exactly to the account limit
    with zero headroom. Dave hand-patched to 7.5GB/1.5GB (10% headroom)
    and re-switched — confirmed live: `nats-stream-init.service`
    active/exited-clean, `QUEUE_TRANSITIONS.max_bytes = 1500000000`.
    This 4-failures-on-one-file count is now recorded in the master plan
    as concrete evidence behind the 2026-07-22 Nix-direction-change
    decision (PP-NIXOS-001), not just accumulated mood.
  - App-code side: `nats_client.py`'s `_ensure_streams()` is now
    strictly read-only (no `add_stream()` calls, `log.error()` on a
    genuine miss). Landed on branch `todo/1638-1639-nats-client-fixes`
    (worktree `/opt/TGW/var/worktrees/1638-1639-nats-client-fixes`,
    commit `b790591`) — **not yet reviewed/stitched to main.**
- **Todo #1639** (`tgw_health`'s NATS check crashing via MCP) — DONE,
  same branch/commit as above. Root cause: `check_nats()`/
  `query_mutations()` called bare `asyncio.run()`, which breaks when
  `mcp_server.py`'s `tgw_health()` MCP tool calls it from inside
  FastMCP's already-running event loop (CLI path never hit this, which
  is why it shipped unnoticed). Fixed with a `_run_isolated()` helper
  (dedicated thread + fresh loop). Live-verified against the real
  broker, reproducing the exact FastMCP nested-loop condition. 18/18
  relevant tests pass; full suite 2756 passed, 2 pre-existing unrelated
  failures found and filed as **new todo #1641** (stale line-number
  allowlist in a C12 static test, confirmed present on base branch too
  — not this session's doing, needs its own look).
- **Todo #1640** (plan the sequencing) — DONE. Plan doc written:
  `docs/ai-plans/jetstream-substrate-buildout.md` — 5-packet sequence
  (A=#1638 done, B=#1639 done, C=acceptance-evidence suite, D=mailbox
  packet, E=LOADTEMP packet), each gated on the one before it.

## Decided this session, encoded in TGW-Master-Plan.md

- **NATS network exposure**: bind on tgw-prod's **Tailscale interface**
  (not localhost-only — current state — and not raw LAN) so a1131 can
  actually reach the broker, reusing PP-REMOTEOPS-001's existing tunnel.
  **Not yet built** — this is Packet C's next concrete step.
- **NATS auth model**: **one account, per-actor subject permissions**
  (not separate accounts per actor) — e.g. `tgw.mailbox.claude.>` scoped
  to Claude's own credential. Simpler than full account isolation,
  still gives a real broker-enforced cross-actor denial. **Not yet
  built** — same Packet C step.
- Both master-plan reconciliations from Tigwa's two notes (facility
  cross-check + mailbox-reliability canonical reconciliation) are folded
  into PP-AIOPS-001/PP-RUNNERCOMMS-001/PP-LOADTEMP-001 sections.

## Still open / next session starts here

1. **Review + stitch** `todo/1638-1639-nats-client-fixes` — hasn't gone
   through `/tgw-runner-review` yet. Do that before merging.
2. **Packet C** (acceptance-evidence suite) — blocked on building the
   Tailscale-bind + per-actor-accounts infrastructure first (both
   decided above, neither built). This is the next real build step in
   the JetStream sequence — write its packet doc, dispatch mixed
   (flake for the bind, likely flake or a small app-code piece for
   account provisioning).
3. **Todo #1641** — pre-existing unrelated test failure, needs triage
   (not blocking, not this session's regression).
4. **Tigwa hasn't responded yet** to the Max-plan-sprint context note
   sent this session (`inbox/tigwa/CLAUDE-NOTE-new-context-for-pp-
   postgres-001-sequencing-month-2026-07-22.md`) — check her mailbox
   response next session before assuming her PP-POSTGRES-001 sequencing
   call still stands unchanged.
5. **No month-scale roadmap written yet** — Dave's "build it all"
   direction is recorded as standing context in the master plan's new
   top section, but there's no single document sequencing all open PPs
   across the month. Worth asking Dave whether he wants that as its own
   planning pass, or whether packet-by-packet (current mode) is fine.
6. Packets D (mailbox) and E (LOADTEMP) remain unscheduled, per the plan
   doc's own "not yet packet-sized" notes — don't jump ahead of C.

## Uncommitted plan-vault state

All of tonight's doc edits (master plan, this note, the new ai-plan doc,
packets/1639, mailbox note to Tigwa) are uncommitted — ask Dave before
committing, per standing feedback.
