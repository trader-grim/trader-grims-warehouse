# Packet #1638 — NATS stream provisioning: eliminate the dual-authority bug

**pp_ref:** PP-AIOPS-001
**Size:** S (single file, well-scoped, no schema/data migration)
**Depends on:** todo #1510 (NATS broker itself is live; this fixes what's
blocking `ITEMDATA_MUTATIONS`'s retention from actually applying)

## Context budget

Read before starting: `src/tgw/apis/nats_client.py` in full (139 lines to
`_ensure_streams`), `~/tgw-flake/nix/nats.nix` in full. Don't read the
rest of PP-AIOPS-001-cat-herding-platform.md unless you need Phase 2's
original `QUEUE_TRANSITIONS` design intent — the subject pattern
(`queue.{queue_name}.{state}`) and retention rationale (30 days) are
already in `nats_client.py`'s own code, that's sufficient.

## Verified-live root cause (2026-07-22, not theorized)

Tonight's `nats-stream-init.service` kept failing with `insufficient
storage resources available (10047)` across three separate fix attempts
(a flag-syntax bug, then a unit-suffix-parser mismatch, then confirmed
byte-exact via a live test stream) — all three fixes were real and
necessary, but none of them could have caught this, because the actual
blocker is a fourth, different bug: **two independent, uncoordinated code
paths both provision the same NATS streams.**

1. `nats.nix`'s `nats-stream-init` systemd oneshot (built earlier tonight,
   todo #1510) is the *declarative* path — it runs after `nats.service`
   starts, and tries to create-or-edit `ITEMDATA_MUTATIONS` with an
   explicit 10,000,000,000-byte ceiling and 90-day retention.
2. `src/tgw/apis/nats_client.py`'s `_ensure_streams()` (pre-existing, not
   touched tonight) is a *second*, independent path — it runs every time
   any worker/API process calls `init_nats()` and successfully connects,
   and creates BOTH `ITEMDATA_MUTATIONS` *and* `QUEUE_TRANSITIONS` if
   they don't yet exist, with `max_age` set but **no `max_bytes` at
   all** — which nats-py/nats-server defaults to `-1` (unlimited). Its
   own code comment is the smoking gun: `"max_bytes -1 = server-side
   limit from nats-server.conf"` — this assumption is **false**. An
   unlimited-`max_bytes` stream is not silently capped to whatever's left
   of the server's global ceiling; per NATS JetStream's own admission
   control (confirmed via Synadia's docs and multiple nats-server GitHub
   issues, cited below), an unbounded sibling stream can block a
   *different* stream's bounded reservation from being granted at all,
   regardless of actual current usage.

Live-confirmed sequence: `_ensure_streams()` created both streams
unbounded (almost certainly the first time any worker reconnected after
tonight's `nats.service` restart, since `stream_info()` found neither
stream present at that point and its own `add_stream()` call has no
`max_bytes` argument to pass). `nats-stream-init.service` then ran and
tried to *edit* the already-existing `ITEMDATA_MUTATIONS` up to its
10GB bound — and that edit is refused, because `QUEUE_TRANSITIONS`
already sits on the account unbounded, worst-case-reserving against the
same shared ceiling. Confirmed live: `nats stream info QUEUE_TRANSITIONS
--json` shows `"max_bytes": -1` right now, and `nats account info` shows
`Storage: 0 B of Unlimited` for the account even though the server itself
was started with an explicit `max_file` ceiling — the account-level view
doesn't reflect the server-level constraint, which is exactly why this
was easy to miss at a glance.

Even setting aside tonight's specific failure: `_ensure_streams()` only
runs its `add_stream()` branch when `stream_info()` fails (stream doesn't
exist yet) — it **never edits an existing stream**. So even after this
packet's fix lands, `QUEUE_TRANSITIONS` would stay unbounded forever
unless something explicitly re-provisions it. Two authorities for the
same resource, silently drifting apart, is the actual bug class here —
the same "reuse, don't invent a second authority" principle this session
already applied elsewhere (E16/E17, tonight's mailbox/JetStream
convergence) — freshly discovered as a new instance, not the general
principle being restated.

## Spec

**Single authority: `nats.nix`'s declarative stream-init owns creation
*and* configuration of every JetStream stream. `nats_client.py` only
publishes — it never creates or edits stream config.**

1. In `~/tgw-flake/nix/nats.nix`'s `nats-stream-init` script, add a second
   stream block for `QUEUE_TRANSITIONS`, same `add`/`edit`-if-exists
   pattern already used for `ITEMDATA_MUTATIONS`:
   - Subjects: `queue.>` (matches `nats_client.py`'s existing
     `SUBJECT_TRANSITION = "queue.{queue_name}.{state}"`)
   - Retention: 30 days (matches `nats_client.py`'s existing
     `max_age": 30 * 86400.0` — don't silently change the retention
     window this packet didn't ask to touch, just make it declarative)
   - `max_bytes`: an explicit bounded value, **not** `-1`. Pick a split
     of the account's 10,000,000,000-byte ceiling between the two
     streams — `QUEUE_TRANSITIONS` carries small per-transition messages
     (state + metadata, no field diffs) at potentially high frequency
     across every `tgw-worker@*` unit, `ITEMDATA_MUTATIONS` carries
     larger per-field-change payloads but lower frequency (gated behind
     Phase 0/todo #1636 actually being wired into `http_server.py` — not
     live yet). A reasonable starting split, not deeply researched: 20%
     `QUEUE_TRANSITIONS` (2,000,000,000 bytes) / 80% `ITEMDATA_MUTATIONS`
     (8,000,000,000 bytes) — **flag this split explicitly as a
     judgment call, not a measured decision**, per Prime Directive 3; if
     real volume data exists anywhere (queue_jobs row counts/day,
     average transition-event size) use it instead of this guess.
   - Update the account-level `jetstream.max_file` comment (already
     present from tonight's earlier fix) to note it must be `>=` the sum
     of every declared stream's `max_bytes`, not just the largest one —
     this is the actual invariant that broke tonight, name it so the
     next person adding a third stream doesn't repeat the mistake.
2. In `src/tgw/apis/nats_client.py`, replace `_ensure_streams()`'s
   `add_stream()` calls with a **read-only check**: call `stream_info()`
   for each stream; if either is missing, `log.error()` (not
   `log.warning()` — a missing stream at this point means the declarative
   provisioning never ran, which is a real operational problem, not a
   quiet first-boot condition) and continue without creating anything.
   Remove the now-wrong `"max_bytes -1 = server-side limit from
   nats-server.conf"` comment entirely — don't leave it as stale
   documentation of a disproven assumption.
3. This is a **mixed packet** — step 1 is a flake change (routes through
   the usual nix-flake-maintainer commit → dry-activate → request-push/
   request-switch flow, per PP-FLAKEGATE-001), step 2 is `src/tgw/`
   application code (routes to `tgw-coder` per invariant E12, isolated
   worktree+branch, not a direct edit). **Do not let one agent touch
   both** — split into two dispatches even though they're one logical
   fix, same as every other cross-boundary change tonight.

## Verification (do not trust dry-activate or a syntax-clean edit alone —
tonight's history on this exact file is 3 fixes that all passed static
checks and still failed live)

1. After step 1 lands (flake side): confirm via `nats stream info
   QUEUE_TRANSITIONS --json` that `max_bytes` is the new bounded value,
   not `-1`.
2. After step 1 lands: confirm `nats stream info ITEMDATA_MUTATIONS
   --json` shows the intended 10GB-minus-QUEUE_TRANSITIONS'-share value
   with no "insufficient storage" error in `journalctl -u
   nats-stream-init.service` for that run.
3. After step 2 lands (app-code side): restart one worker
   (`systemctl restart tgw-worker@echo.service` — the designated
   low-risk testbed worker, matches this session's earlier bubblewrap-
   pilot reasoning) and confirm via its journal that `init_nats()` logs
   the read-only `stream_info()` check succeeding for both streams, with
   no `add_stream` call attempted.
4. Full round-trip: publish one real test message via
   `tgw.apis.nats_client.publish_mutation()` (or the transition
   equivalent) from a live Python shell, confirm it lands in the correct
   stream with `nats stream info` showing message/byte counts moving.

## Out of scope

- Actually wiring `publish_mutation()` into `http_server.py` — that's
  todo #1636 (Phase 0), a separate packet.
- Re-deriving the 20/80 storage split from real measured volume — flagged
  above as a judgment call; revisit if either stream's real usage
  approaches its bound and starts rejecting writes.
- Any change to `ITEMDATA_MUTATIONS`'s retention window or the account's
  total 10GB ceiling — both already decided tonight, unchanged here.

## Sources for the root-cause claim (external, cited per the live search
that found them)

- Synadia, "NATS Streams Without Limits: What It Means and How to Fix
  It" — https://www.synadia.com/insights/checks/nats-streams-without-limits
- nats-io/nats-server#3321 — "insufficient storage resources available
  [10047] when updating stream to the same config"
- nats-io/nats-server#4281 — "could not create Stream: insufficient
  storage resources available (10047)"
