# IN PROGRESS — 2026-07-22 planning-week/NATS-Syncthing session wrap — resume here

**Session covered:** eBay-DS-1077 reply rewrite, mailbox reliability
redesign (delivery guarantee/reply trail/versioned drafts/
compartmentalization), NATS/JetStream convergence + build, Syncthing
propagation-gap diagnosis + fix, PP-LOADTEMP-001 (system-load "weather
station"), fence-bypass investigation → PP-POSTGRES-001 5-phase plan,
and a major standing-direction change on Nix itself. Long session, dense
— read this in full before doing anything, several threads are still
live.

## Flake state — all clean as of session end

`tgw flake queue` is empty. All four commits from tonight
(`f34c8fc` config-backup timer, `fb61b58` Syncthing folder declaration,
`bc2b67c` NATS 10GB retention — had a units bug, `c4e942d` the actual
byte-integer fix) are pushed to `origin/master` and live on tgw-prod
(`readlink /run/current-system` confirmed matching). All 8 push/switch
jobs marked executed, audit trail matches reality. **Do not re-request
push/switch for these — they're done.**

## Still broken, live right now

`nats-stream-init.service` is `failed` on tgw-prod. This is **not** the
retention-config bug from earlier (that's genuinely fixed) — it's a
**dual-authority bug**: `src/tgw/apis/nats_client.py`'s
`_ensure_streams()` (pre-existing, untouched tonight) independently
creates both JetStream streams at worker startup with no `max_bytes`
(defaults unbounded), uncoordinated with the new declarative
`nats.nix` provisioning — and NATS's own admission control lets an
unbounded sibling stream (`QUEUE_TRANSITIONS`) block a different,
bounded stream's reservation regardless of actual usage. **Full packet
already drafted and ready to dispatch:**
`packets/1638-nats-stream-single-authority.md` (todo #1638). Mixed
packet — flake side (nix-flake-maintainer) + `src/tgw/` side
(tgw-coder), do not let one agent touch both.

## Todos opened/touched this session, current state

- **#1510** (PP-AIOPS-001) — NATS broker itself: DONE, live, verified.
  Stays open only because Phase 1's actual point (the audit stream
  working end-to-end) isn't proven until #1638 lands — don't close it
  prematurely.
- **#1632** (PP-DATAINTEGRITY-001) — Syncthing folder declared + live,
  **marked done this session**. One honest gap: could not independently
  confirm an actual completed sync cycle via REST API (tooling friction
  in this sandbox, not a real blocker) — worth a quick check next
  session that both hosts' copies of `/home/db/Sync` actually match.
- **#1636** (PP-POSTGRES-001 Phase 0) — wire `publish_mutation()` into
  `http_server.py`'s real fence. Spec-only, not started, small/
  independent, zero schema risk. Packet-ready.
- **#1637** (PP-DATAINTEGRITY-001) — new invariant C15, static test
  cloned from C12's pattern. Spec-only, not started. First fix target:
  `items.py`'s `verifiedupdate()`.
- **#1638** (PP-AIOPS-001) — the dual-authority NATS fix above. Full
  packet drafted, not dispatched.
- **#1626/#1628** (PP-WORKFLOW-001/PP-CLASSIFIER-001) — pre-existing,
  fully scoped from an earlier session, still genuinely ready to
  delegate to `tgw-coder`, untouched tonight.
- **#1630** (PP-PORTABLE-CATALOG-001) — a1131 GUI-launch verification
  still held/pending, unrelated to tonight's other threads, still open.
- **#1634** (PP-AIOPS-001 Phase 5 REVISED) — bubblewrap worker-fleet
  survey, not started.

## Major standing decision this session — read before touching Nix again

**Direction changed, not just a mood.** Dave: "We are changing unless we
find a good reason not to. To what and when TBD." Full evidence trail
(both the 2026-07-14 original "mull" entry and tonight's cost tally —
three failed fixes on one file, a live desktop-input disruption, a
still-open dual-authority bug, all while `dry-activate` passed clean
every time) is in `TGW-Master-Plan.md`'s `PP-NIXOS-001` section, and in
memory `project-nix-stability.md` (flagged at the top of that file).
**Target OS and timeline are explicitly undecided** — this doesn't
authorize starting a migration, but new Nix-flake work should be weighed
against a flipped default (staying needs a reason now, not leaving).
Direct unresolved tension on record: `PP-NIXOS-001` (migrate onto NixOS)
vs. this new direction (migrate off Nix) — both exist simultaneously,
needs Dave's reconciliation at a real planning pass.

## Other design work from tonight, not yet packet-ready

- **PP-RUNNERCOMMS-001 mailbox redesign** — delivery guarantee (Postgres-
  backed, not Syncthing-file-based), reply trail (`parent_message_id`),
  versioned drafts (E14-shaped, never overwrite in place),
  compartmentalization (per-actor access boundary must survive the
  redesign, not just be a filesystem convenience). Converged onto the
  same JetStream broker as the audit stream/`agent_handoff`. Sent to
  Tigwa as a REVIEW request — check `inbox/tigwa/` for her response
  before scoping further.
- **PP-LOADTEMP-001** — system-load "weather station," per-host, polled
  (not event-stream), structured multi-field reading PLUS a derived
  single "degrees" number for quick capacity decisions, atomic
  integration into `claim_queue_jobs()`. Real open questions: the
  derived-number formula, degrees→concurrency lookup table, per-job-type
  field rules, how a throttled worker signals its state visibly. Fully
  shaped, not scoped into a packet.
- **PP-POSTGRES-001** — now a real 5-phase plan (Phase 0 = todo #1636
  above; Phase 4 = the actual unbypassable fence, column-level
  `GRANT`/`REVOKE`). Full writeup in `pp/PP-POSTGRES-001.md`.

## Scouting-report items from earlier tonight, still untouched

From a full master-plan sweep for "unintegrated" pieces — items #1
(NATS/Radar) and #2 (QueueStats/AIOPS dedup) are resolved. Still open:
- **#3** (fence-bypass bug cross-check) — resolved as part of tonight's
  PP-POSTGRES-001 work, `verifiedupdate()` confirmed live, #1377 still
  genuinely open. Can consider this one closed too.
- **#4** — PP-WORKFLOW-001 doesn't consider the settled LISTEN/NOTIFY
  event bus as a dispatch mechanism.
- **#5** — PP-CODEGRAPH-001's Z3 invariant catalog vs. PP-CLASSIFIER-001's
  rule registry, two designs converging without citing each other.
- **#6** — PP-ROUTER-001's NATS-alarm leg, sent to Tigwa for
  reconciliation before tonight's NATS decisions existed — probably
  quick to answer now.
- **#7** — PP-OUTBOX-001 describes the same E14-versioned-draft shape as
  tonight's mailbox redesign without citing it.

## Pending: Tigwa review

Sent her a broad review request this session covering tonight's session
work generally, cross-checked against her own project list and the
facility-buildout work — check `inbox/tigwa/` for anything she's sent
back, and `inbox/claude/` for her response when it lands.

## Not committed

All of tonight's plan-vault doc changes (master plan, PP-POSTGRES-001,
PP-AIOPS-001 doc, register, new packet, archived inbox notes) are
uncommitted as of session end — Dave's call each time, ask before
committing.
