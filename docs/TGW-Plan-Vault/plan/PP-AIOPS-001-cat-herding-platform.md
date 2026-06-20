# PP-AIOPS-001 — Structured AI/Operational Resilience Platform
### aka "The Cat Herding and Litterbox Cleaning Platform"

**Opened:** 2026-06-19 (session 35)
**Status:** PROPOSAL — awaiting Dave's review and go signal
**Maintained by:** Claude (Opus for design, Sonnet for implementation slices)
**Research basis:**
- `reference/PP-AGISOLATION-001-perplexity-research.md` — Perplexity synthesis (2026-06-19)
- `inbox/nats-jetstream-btrfs-transactional-processing-and-resiliancy.md` — source doc 1
- `inbox/btrfs-nixos-nspawn-tranactional-ai-safety-layer.md` — source doc 2
- `inbox/nixos-resilient-ai-development and automation-platform.md` — source doc 3
- `plan/AUDIT-2026-06-19.md` — system audit findings that motivated this proposal

---

## The Problem in Plain Language

For the past month TGW operated blind — no eBay data locally, no audit trail for data
changes, no way to trace who changed what or why a field has an unexpected value. When
things went wrong (shipping data missing, 619 photos renamed, data regressions) we had
no record to reconstruct events from. We discovered problems by noticing symptoms, not
by observing causes.

The platform has two classes of actor making changes to production data:
1. **Workers** — deterministic, bounded, systemd-managed, PostgreSQL-tracked
2. **AI agents** — Claude sessions, Aider tasks — unbounded, no audit trail, no rollback

We need to make both classes first-class citizens of the state machine: every mutation
logged, every AI session scoped and recoverable, every known failure pattern
automatically cleaned up.

**Cat herding** = keeping AI agents and workers from tripping over each other or making
unobserved messes.
**Litterbox cleaning** = detecting messes automatically, fixing the known ones without
operator intervention, escalating the unknown ones.

---

## Design Goals

1. **Complete mutation audit** — every field write to ItemData, every queue state
   transition, has a timestamped record with source attribution. No change is invisible.
2. **AI session isolation** — a Claude or Aider task runs in a bounded sandbox; its
   changes are visible before they're committed to production; it can be rolled back
   as a unit.
3. **Automated cleanup** — a library of known failure patterns and their repairs runs
   continuously; common messes are fixed without paging the operator.
4. **Operational observability** — the operator can ask "what changed in the last hour"
   or "what did that Aider session touch" and get a complete answer.
5. **No new canonical state store** — PostgreSQL remains the sole source of truth for
   the state machine. Nothing in this design changes that invariant.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      TGW Data Layer                             │
│                                                                 │
│  ItemData writes ──► items._write_field()                       │
│                             │                                   │
│                             ▼                                   │
│                    JetStream audit stream                        │
│                    itemdata.{sku}.{field}                        │
│                             │                                   │
│               ┌─────────────┼─────────────┐                     │
│               ▼             ▼             ▼                     │
│         Anomaly         Litterbox      MCP audit                │
│         detector        worker         tools                    │
│               │             │                                   │
│               └──── alert / auto-fix / escalate ───►  operator │
│                                                                 │
│  PostgreSQL queue_jobs ──► outbox ──► JetStream                 │
│                            queue.{name}.{state}                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    AI Session Layer                             │
│  (Phase 5 — requires PP-NIXOS-001)                              │
│                                                                 │
│  tgw-aider / Claude task                                        │
│          │                                                      │
│          ▼                                                      │
│  pre-task Btrfs CoW snapshot ──► ephemeral nspawn container     │
│              (--private-network)         │                      │
│                                     task runs                   │
│                                          │                      │
│                                   FIFO pipe / stdout            │
│                                          │                      │
│                               ◄──────────┘                      │
│                        HOST SUPERVISOR                          │
│                        reads output, validates scope            │
│                               │                                 │
│              ┌────────────────┤                                 │
│              ▼ clean          ▼ anomaly detected                │
│         promote snapshot    kill + preserve snapshot            │
│         to live             + alert operator                    │
│              │                                                  │
│         publish mutations to JetStream (host side only)         │
└─────────────────────────────────────────────────────────────────┘
```

> **Container isolation constraint (from source research):** JetStream is accessed only
> from the HOST — never from inside containers. Containers run `--private-network`.
> Container→host communication is via FIFO named pipe or stdout capture.
> The host supervisor reads the result and publishes to NATS. NATS KV CAS locking
> FROM INSIDE containers was considered and rejected — `--private-network` blocks it.
> The outbox/broadcast pattern survives because publishing is always host-side.

---

## Component Decisions

### NATS JetStream — role and scope

**Dave's intent (clarified 2026-06-19):** JetStream is the transactional log for data
modifications — a CDC (change data capture) layer, not a state machine. It is NOT
replacing PostgreSQL and NOT used for locking or concurrency control.

What JetStream provides here:
- Ordered, persistent, replayable stream of every ItemData field mutation
- Ordered stream of every PostgreSQL queue state transition (outbox pattern)
- Subscriber model: multiple consumers (anomaly detector, litterbox, MCP query) without
  coupling them to the write path
- Time-windowed replay: "show me everything that changed between T1 and T2"

What JetStream does NOT do here (per Perplexity viability findings):
- NOT canonical state (PostgreSQL owns that)
- NOT advisory locks (PostgreSQL advisory locks or the existing state machine handles this)
- NOT "read your writes" linearizable — acceptable because it is a log, not a query store.
  Consumers treat it as eventually consistent observability, not as a decision oracle.

**Stream design:**
```
Stream: ITEMDATA_MUTATIONS
  Subject: itemdata.{sku}.{field}
  Retention: limits (90 days or 50 GB, whichever first)
  Replication: 1 (single-node initially; upgrade with NATS cluster if needed)
  Each message payload:
    { sku, field, old_value, new_value, source, timestamp, session_id }

Stream: QUEUE_TRANSITIONS
  Subject: queue.{queue_name}.{state}
  Retention: limits (30 days)
  Each message payload:
    { job_id, queue_name, old_state, new_state, entity_id, error_code, timestamp }
```

`source` values for attribution:
- `worker:{name}` — a systemd worker (e.g., `worker:ebay_draft`)
- `api:operator` — direct tgw CLI command by the operator
- `ai:claude:{session_id}` — Claude session
- `ai:aider:{task_id}` — Aider task
- `bulk_edit` — bulk operation
- `ebay_pull` — sync from eBay

### PostgreSQL — unchanged canonical role

Queue state machine, `todo_items`, all transactional writes: unchanged.

Addition: a lightweight outbox trigger or application-level publish in `QueueWorker`
completes/fails/dead-letters a job. The outbox publishes to `QUEUE_TRANSITIONS`; failure
does not block the job transition (fire-and-forget to JetStream, idempotent on retry).

### Btrfs snapshots — dual role

Already running as PP-BACKUP-001 (hourly snapshots of `/opt/TGW`).

New role in Phase 5: pre-task snapshots for AI session isolation. These are the same
mechanism, used with shorter TTL (discard after task completes cleanly, retain on anomaly).
No new Btrfs infrastructure needed — just a new consumer of the existing snapshot tooling.

**Critical layout requirement (Perplexity finding):** nspawn `--ephemeral` only creates
a CoW snapshot if the directory is a Btrfs subvolume. The AI task sandbox template must
be a proper subvolume, not just a directory. This must be validated before Phase 5 lands.

### systemd-nspawn — AI sandbox runtime (Phase 5)

Each AI task runs in a container with:
- `/opt/TGW/src` — read-write inside the snapshot (container sees a private CoW copy)
- `/opt/TGW/data/ItemData` — read-write inside the snapshot
- `/opt/TGW/secrets` — read-only bind mount (never copied, always the real secrets)
- Network: host networking allowed (eBay API calls, Ollama, OpenRouter)
- On anomaly: SIGKILL the container, preserve the snapshot as an audit subvolume

Phase 5 gates on PP-NIXOS-001 because declarative NixOS containers make the sandbox
definition reproducible and versionable in the flake. Running nspawn manually on MX Linux
is possible but not worth the operational complexity for a temporary state.

### Anomaly Detection Worker (`anomaly_detector`)

A new worker, queue `anomaly_detector`, subscribes to both JetStream streams and applies
a rule library. Rules fire events; events go to `notify()` and the litterbox worker.

Initial rule library:
```
MUTATION_RULES:
  - price_set_zero: price field written as 0 or "" by any non-operator source
  - status_regressed: status transitions backward (Active → unknown, In Stock → new)
  - primary_photo_renamed: SKU.jpg ceases to exist after any write event
  - field_thrash: same field written >5x in <5 minutes by the same source
  - bulk_status_change: >50 status changes in <1 minute from a single source

QUEUE_RULES:
  - dead_letter_spike: >10 dead-letters in <10 minutes on any queue
  - lease_expiry_cluster: >5 LEASE_EXPIRED on same queue in <5 minutes
  - retry_wait_accumulation: >20 retry_wait jobs on same queue
```

Each triggered rule emits:
```json
{
  "rule": "price_set_zero",
  "severity": "critical|warning|info",
  "affected_skus": ["tgw..."],
  "source": "worker:ebay_draft",
  "timestamp": "...",
  "auto_fixable": true,
  "litterbox_action": "restore_from_audit"
}
```

### Litterbox Worker (`litterbox`)

A new worker, queue `litterbox`, receives anomaly events and applies the auto-fix library.
Fix results are published back to JetStream as `litterbox.{rule}.{result}`.

**Existing behaviors that become formal litterbox actions:**

| Known mess | Current handling | Litterbox action |
|-----------|-----------------|-----------------|
| 503 dead-letters | Manual requeue | Auto-requeue after 1h |
| Rate-limited dead-letters | Manual | Auto-requeue with 6h delay |
| Negative qty | `itemdata_scrub` (undeployed) | Deploy scrub + auto-repair |
| Renamed primary photo | `photo_history_recovery` (undeployed) | Deploy + pull from ebay_live.imageUrls |
| Stale TEMPLATE: prefix in title | catalog-verify --fix | Auto-fix on scan |
| LEASE_EXPIRED failed jobs | Ignored | Auto-requeue if queue is otherwise healthy |
| offline_draft_stall | catalog-verify warning | Alert after 24h; auto-escalate after 72h |

**New litterbox actions (post Phase 3):**

| Triggered by | Action |
|-------------|--------|
| price_set_zero | Restore price from most recent `ebay_live.pricingSummary.price` |
| status_regressed | Log + alert; hold for operator unless regression is from a known pattern |
| dead_letter_spike | Alert immediately; pause enqueueing to affected queue |
| field_thrash | Log session_id, alert, flag session for review |

### Platform MCP Extensions (Phase 4)

Extend `tgw-mcp-server` with new read-only audit tools:

| Tool | Purpose |
|------|---------|
| `tgw_audit_trail` | Full JetStream history for a SKU — every field change, source, timestamp |
| `tgw_session_diff` | All changes attributed to a given session_id |
| `tgw_rollback_session` | Revert all mutations from a session (uses audit trail as inverse patch) |
| `tgw_anomaly_log` | Recent anomaly events and their auto-fix disposition |
| `tgw_litterbox_log` | Recent auto-cleanups with before/after field values |
| `tgw_mutation_rate` | Mutations per minute, by source — operational heartbeat |

---

## Phases

### Phase 1 — JetStream Install + ItemData Audit Stream
**Gate:** Operator installs NATS (Docker or native); no PP-NIXOS-001 dependency.
**Size:** M (1–2 sessions)

1. Install NATS server (single-node JetStream mode); add to `tgw-api-config.json`
2. Add `tgw.apis.nats` client module (`nats-py` dependency)
3. Wire `items._write_field()` and `items.write_item()` to publish to `ITEMDATA_MUTATIONS`
   — fire-and-forget; if NATS is down, write succeeds silently (no hard dependency)
4. `tgw audit-trail <SKU>` CLI command — queries JetStream, prints field history
5. Health check extension: NATS connectivity + stream age
6. Tests: mock NATS publish in write-field tests; integration test with real stream

**Output:** Every ItemData mutation from this point forward has a log entry. Historical
data is not backfilled (too large; not needed — the audit is forward-looking).

---

### Phase 2 — Queue Transition Outbox
**Gate:** Phase 1 complete.
**Size:** S (1 session)

1. Add outbox publish in `QueueWorker._complete()`, `._fail()`, `._dead_letter()`,
   `._requeue_with_backoff()` — publishes to `QUEUE_TRANSITIONS`
2. Add session_id concept: environment variable or config that workers inherit from
   their systemd unit; AI tasks get a generated session_id at spawn time
3. `tgw queue-stream [--queue NAME] [--since T]` CLI — live tail or replay of transitions
4. Tests: verify outbox publish is called; verify fire-and-forget (NATS down = no exception)

**Output:** Queue state machine is now fully observable via JetStream without polling
PostgreSQL. The existing health/dashboard endpoints remain, but JetStream becomes the
streaming view.

---

### Phase 3 — Anomaly Detection Worker
**Gate:** Phase 2 complete (needs QUEUE_TRANSITIONS stream).
**Size:** M (1–2 sessions)

1. New worker `workers/anomaly_detector.py` — subscribes to both streams, applies rule
   library, emits anomaly events
2. Anomaly events published to `ANOMALIES` stream (consumed by litterbox + MCP)
3. `notify()` integration: critical anomalies fire desktop notification immediately
4. Rule library: implement the 8 initial rules (see Component Decisions above)
5. `tgw anomaly-log [--severity critical] [--since T]` CLI
6. Tests: inject synthetic mutation events; verify each rule fires correctly

**Output:** The platform now observes itself. Known bad patterns are detected within
seconds of occurring, not discovered by operator inspection hours later.

---

### Phase 4 — Litterbox Worker + MCP Audit Tools
**Gate:** Phase 3 complete.
**Size:** L (2–3 sessions)

1. Deploy `workers/itemdata_scrub.py` as `tgw-worker@itemdata_scrub.service` — this
   already exists and handles field normalization; wire it to the anomaly event stream
2. Deploy `workers/photo_history_recovery.py` as `tgw-worker@photo_history_recovery.service`
3. New worker `workers/litterbox.py` — subscribes to `ANOMALIES`, applies auto-fix library
4. Initial fix library covers: 503/rate-limit requeue, photo rename repair, negative qty,
   LEASE_EXPIRED requeue, stale template prefix
5. Extend `tgw-mcp-server` with: `tgw_audit_trail`, `tgw_session_diff`, `tgw_anomaly_log`,
   `tgw_litterbox_log`, `tgw_mutation_rate`
6. One-time repair run: feed the 619 photo-rename victims through `photo_history_recovery`
7. Tests: each fix action has a test with a known-bad input and expected output

**Output:** The platform now fixes itself for the known mess patterns. The MCP tools give
Claude and Aider full visibility into what changed during any session.

---

### Phase 5 — AI Session Isolation (nspawn + Btrfs)
**Gate:** PP-NIXOS-001 Phase 3 complete (NixOS running on spare hardware); Phase 4 done.
**Size:** L–XL (2–4 sessions + operator validation)

1. Define NixOS container template in `nix/aiops-sandbox.nix`:
   - Ephemeral nspawn container, tmpfs root
   - `/opt/TGW/src` and `/opt/TGW/data` as Btrfs CoW mounts (subvolumes required)
   - `/opt/TGW/secrets` read-only bind mount
   - `session_id` injected as environment variable
2. Pre-task hook: `tgw sandbox-create <task_id>` — Btrfs snapshot of src + data
3. `tgw sandbox-run <task_id> -- <command>` — launches nspawn with the snapshot mounts
4. Post-task validation: query JetStream `ITEMDATA_MUTATIONS` for `session_id=<task_id>`;
   compare against expected change scope (provided at task creation)
5. Commit path: `tgw sandbox-commit <task_id>` — promotes snapshot to live, discards container
6. Rollback path: `tgw sandbox-rollback <task_id>` — discards snapshot, preserves audit copy
7. MCP tool: `tgw_rollback_session` — callable from Claude/Aider to self-rollback a bad run
8. Subvolume layout validation (Perplexity requirement): confirm nspawn `--ephemeral` on
   the template creates a true Btrfs CoW snapshot, not a copy fallback

**Host-level sandbox supervisor daemon** (from source research — concrete implementation):

The supervisor runs on the host as a systemd service. It does two things concurrently:

1. **cgroup v2 resource monitor** — reads
   `/sys/fs/cgroup/machine.slice/container-{name}.scope/memory.current` and `cpu.stat`
   every 1.5 seconds. If memory or CPU spikes past configured thresholds for 3 consecutive
   readings, it triggers the recovery trap (stop container, Btrfs snapshot, restart clean).

2. **FIFO telemetry listener** — a named pipe at `/var/lib/tgw/sandbox/{task_id}/telemetry`
   is bind-mounted read-write into the container at `/tmp/telemetry`. The AI task script
   can write a JSON payload to this pipe to voluntarily signal the host:
   ```json
   {"status": "CRITICAL_TRIP", "reason": "test suite failed", "metadata": {...}}
   ```
   The host reads this and triggers the same recovery trap sequence. This gives the AI
   session a way to self-report problems before they show up in resource metrics.

Startup is handled by a dedicated systemd unit (`tgw-sandbox-supervisor@{task_id}.service`)
spawned by `tgw sandbox-run`, and cleaned up on `sandbox-commit` or `sandbox-rollback`.

**Integration with Aider:** `tgw-aider-step` skill (already exists, `7844e0a`) becomes
a thin wrapper around `sandbox-run`. Every Aider task automatically gets a sandbox.

**Output:** AI agent changes are isolated until explicitly committed. Bad sessions can be
rolled back in one command. The "photo rename disaster" class of problem is structurally
prevented — the change is staged in the sandbox and validated before committing.

---

### Phase 6 — Session Rollback + Full Observability
**Gate:** Phase 5 complete.
**Size:** M (1–2 sessions)

1. `tgw_rollback_session` MCP tool: uses the JetStream audit trail as an inverse patch —
   replays mutations in reverse to restore previous values
2. `tgw session-log` CLI: list all sessions (AI and worker), their change counts, status
3. Scheduled anomaly report: daily digest of anomalies, auto-fixes, and open escalations
   delivered via `notify()` or written to the FILING-LOG
4. Litterbox metrics added to `tgw health` output: fixes_today, open_escalations,
   last_clean_run

---

## Dependency Map

```
PP-NIXOS-001       ──gates──► Phase 5 (nspawn sandbox)
PP-BACKUP-001      ──reuses──► Phase 5 (Btrfs snapshot tooling)
PP-DATA-OWN-001    ──feeds──► Phase 4 (photo repair pulls from ebay_live.imageUrls)
PP-MCP-001         ──extends──► Phase 4 (new audit tools on existing MCP server)
PP-MULTIMODEL-001  ──herded by──► Phase 5 (Claude/Aider sessions get sandboxes)
PP-DEADLETTER-001  ──feeds──► Phase 4 (dead-letter classification → litterbox rules)
```

Phases 1–4 have NO dependency on PP-NIXOS-001 and can run on MX Linux today.

---

## What This Unblocks

| Current problem | Fixed by phase |
|----------------|----------------|
| No audit trail for data changes | Phase 1 |
| Can't trace which session caused a regression | Phase 1 + 2 |
| 619 photo-rename victims not repaired | Phase 4 |
| 50 ebay_upload 503 dead-letters | Phase 4 (auto-requeue) |
| itemdata_scrub sitting undeployed | Phase 4 |
| Shipping data gap undiscoverable without manual scan | Phase 3 (anomaly detection flags it) |
| AI tasks unbounded (can change anything) | Phase 5 |
| Bad Aider task requires manual revert | Phase 5 + 6 |

---

## What This Does NOT Do

- Does not replace PostgreSQL state machine
- Does not add new eBay API integrations (those are PP-DATA-OWN-001)
- Does not handle AI model selection (that is PP-MULTIMODEL-001)
- Does not address PP-REPRICER-001 (blocked on eBay scope, unrelated)
- Phase 5 does not improve inference performance (PP-HARDWARE-001 / GPU upgrade)

---

## Perplexity Research Integration

The research (`reference/PP-AGISOLATION-001-perplexity-research.md`) confirmed three
things that shaped this design:

**1. JetStream KV is not linearizable** — resolved by Dave's clarification. JetStream
here is a log, not a lock store. Ordering and persistence are sufficient; linearizability
is not required for a CDC audit stream.

**2. `--ephemeral` requires Btrfs subvolumes** — incorporated as an explicit validation
gate in Phase 5 step 8. The sandbox template directory MUST be a Btrfs subvolume, not
a plain directory. This is a pre-flight check before Phase 5 lands.

**3. MCP server SQL injection risk** — already avoided. `tgw-mcp-server` exposes
domain-specific tools, not raw SQL. The new audit tools added in Phase 4 follow the
same pattern (query JetStream/PostgreSQL internally, return structured domain data).

The Perplexity Alternative 2 (Postgres-centric event sourcing, JetStream as optional
transport) aligns exactly with this design. PostgreSQL owns canonical state. JetStream
is the streaming observability layer. They are not in competition.

---

## Alternatives Evaluated and Rejected

These were explored in the source research conversations before the design above was settled.

### Redpanda instead of NATS JetStream

Redpanda is a C++ Kafka-compatible log bus with zero JVM and fast startup. It was the first
alternative considered. Rejected because NATS JetStream is lighter (~20 MB RAM vs Redpanda's
heavier footprint), has no external dependencies, and includes built-in KV/Object stores. For
a single-node TGW deployment the Kafka API compatibility Redpanda offers is unnecessary overhead.

If TGW ever scales to multi-node or needs to integrate with external Kafka consumers, Redpanda
is a viable swap — the NATS and Redpanda APIs differ, but the architectural role is identical.

### NATS KV Compare-And-Swap as a distributed lock

The research explored using NATS JetStream's Key-Value store with CAS (Compare-And-Swap) as
a distributed lock for AI agent state transitions — specifically to prevent two agents from
modifying the same item simultaneously.

**Negated by VM isolation.** Containers run `--private-network`. A container would need NATS
network access to acquire or release a CAS lock, which requires either routing around the
private network or weakening isolation. Neither is acceptable.

**Resolution:** PostgreSQL handles all transactional locking (SELECT FOR UPDATE, queue_jobs
`started_at` lease, `dedupe_key` uniqueness). NATS is never consulted by code inside a
container. Locking and concurrency control remain entirely in PostgreSQL, consistent with
TGW's settled architecture ("PostgreSQL is the work ledger").

### Temporal.io as a durable execution framework

Temporal provides durable AI workflow execution — if the host crashes mid-LLM-call, Temporal
resumes the workflow exactly where it left off. The research evaluated it as an alternative
to custom state machine logic for AI agents.

**Rejected for TGW.** TGW's `QueueWorker` base class + PostgreSQL `queue_jobs` already
provides durable execution for all workers. AI sessions that crash leave their sandbox
snapshot intact; Phase 5's `sandbox-rollback` recovers the clean state. Adding Temporal
would mean a third state store alongside PostgreSQL and JetStream, with no new capability.
The `QueueWorker` pattern is TGW's Temporal equivalent.

### Docker instead of systemd-nspawn

The research compared Docker/Podman, NixOS MicroVMs, and systemd-nspawn.

**Decision: nspawn now, Docker when the GPU arrives.** Key factors:
- nspawn has no persistent root daemon (Docker's daemon is a privilege escalation risk)
- nspawn shares host /nix/store with zero disk overhead; Docker copies full layers
- GPU passthrough with nspawn requires manual /dev bind-mounts that break on driver updates;
  Docker's `--gpus all` with NVIDIA Container Toolkit handles this seamlessly
- When the GPU is added, `pkgs.dockerTools` in the Nix flake can output a Docker image
  with Nix-pinned dependencies, preserving reproducibility while gaining GPU convenience

MicroVMs (Firecracker via microvm.nix) were also considered. Stronger isolation (separate
kernel) but PCIe GPU passthrough is complex and CPU-only boot takes ~150–200 ms vs <20 ms
for nspawn. Rejected for now; revisit if untrusted code execution becomes a concern.

---

## Open Questions for Dave Before Phase 1 Starts

- **NATS install preference:** Docker container managed by systemd, or native NATS
  binary installed directly on the host? Docker adds a dependency; native is simpler
  on NixOS (NATS is in nixpkgs). For MX Linux now, a single NATS binary is easiest.

- **Audit stream retention:** 90 days / 50 GB suggested. Is there a regulatory or
  operational reason to keep longer? ItemData mutations are potentially high-volume
  (~55k items × avg 10 fields = significant churn during bulk operations).

- **Session ID for operator actions:** Should operator CLI commands (`tgw bulk`,
  `tgw hint`, etc.) generate a session_id so they're groupable in the audit trail?
  Recommended yes — it makes "what did I do last Tuesday" answerable.

- **Litterbox autonomy level:** For Phase 4, auto-fix without confirmation for info/warning
  severity; hold critical severity changes for operator approval before applying? Or
  auto-fix everything and just log it? The conservative default is: auto-fix
  info/warning, queue critical for operator ack.

- **Phase 5 timing:** Phase 5 gates on PP-NIXOS-001. Is there value in a lightweight
  Phase 5 prototype on MX Linux (manual nspawn invocation, no NixOS module) to validate
  the sandbox mechanics before NixOS migration?
