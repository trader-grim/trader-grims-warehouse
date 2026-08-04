# Packet: Agent trace integrity hardening (Phase 1b) — DRAFT, NOT YET AUTHORIZED
Todo: #1586   PP: PP-AGENTTRACE-001   Track: hardening / anti-cover-up controls

**Status: design draft awaiting Dave's sign-off. Do not dispatch to tgw-coder
until Dave explicitly confirms — this packet exists so the concrete shape of
the change is reviewable before any code is written, per the same
plan/invariant-first discipline as every other packet, and because this one
specifically follows a security-relevant review (todo #1585) that asked for
a design proposal, not implementation authorization.**

Origin: Tigwa's authenticity/anti-cover-up review request
(`CLAUDE-REQUEST-agent-trace-authenticity-review-2026-07-20.md`, archived),
Claude's response (todo #1585, sent to Tigwa's inbox 2026-07-20), and Dave's
refinement in-session same day: "what if we made the writes small and hash
verified so changes can be detected? Another mechanism... syncthing's file
versioning to an external write-only location."

## Context budget (ALL the model may load, once authorized)
This packet + `src/tgw/logging.py`'s `archive_transcript()` (Phase 1, merged)
+ `src/tgw/queue/state_machine.py`'s `agent_runs` DDL / `start_agent_run()` /
`end_agent_run()` / `get_agent_run()` (Phase 1, merged) + Tigwa's review
request + Claude's response (both in `docs/TGW-Plan-Vault/inbox/tigwa/` or
archived — read for full gap analysis, don't re-derive it). Nothing else.

## Verified live before this packet was written
- `archive_transcript()` (`logging.py:236`) is explicitly re-entrant: calling
  it twice for the same `run_id` atomically replaces that run's own copy — no
  hash, no lock, no distinction between a legitimate retry and a malicious
  post-hoc swap. This is the single highest-priority gap named in todo #1585.
- No content hash exists anywhere in `agent_runs` or the archived-transcript
  layer today.
- `parent_run_id` (`state_machine.py`, `agent_runs` DDL) is a self-declared FK
  with no validity check against the claimed parent's actual state.
- Syncthing is already running dual-instance on tgw-prod/a1131 (db=8384/22000/
  21027, tgw=8385/22001/21028 — see `reference-syncthing-nix-ports` memory /
  `nix/tgw/platform.nix`) — no new sync infrastructure needs to be stood up,
  only a new folder + versioning config on the existing `tgw` Syncthing
  instance. **This part is a `~/tgw-flake` change and belongs to
  `nix-flake-maintainer`, not `tgw-coder`** — flagged explicitly below, this
  packet's `tgw-coder` scope is the Python/DB side only.

## Spec (two independent legs — Python/DB leg for tgw-coder, flake leg for nix-flake-maintainer)

### Leg A — content hash-commitment (tgw-coder, `src/tgw/`)
1. New table (or new columns on `agent_runs` — pick whichever is cleaner once
   actually scoped; a separate `agent_run_transcript_hashes` table with
   `run_id TEXT PRIMARY KEY REFERENCES agent_runs(run_id)`,
   `sha256 TEXT NOT NULL`, `committed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
   is the cleaner option — keeps the hash-commitment as its own small,
   append-only surface rather than adding a mutable-looking column to the
   main table). No UPDATE path on this table at all — insert-only, enforced
   by not writing any update function for it, not just by convention.
2. `archive_transcript()` (or a new wrapper around it) computes the SHA-256
   of the copied file content at archival time and inserts the commitment
   row. **The commitment insert must fail (and the whole operation must
   report failure) if a commitment already exists for that `run_id`** — this
   is the actual lock: `archive_transcript()`'s existing overwrite-on-retry
   behavior is preserved for the *file* (so a genuinely failed partial copy
   can still be retried), but a *second successful* archival for a `run_id`
   that already has a committed hash must be rejected, not silently applied.
3. New verification function, e.g. `verify_transcript_hash(run_id) ->
   {"ok": bool, "expected": str, "actual": str|None, "error": str|None}` —
   re-reads the archived file, re-hashes, compares against the committed
   value. Used by both a future reconciliation job and ad-hoc manual checks.
4. `parent_run_id` validity: `start_agent_run()` rejects (raises, does not
   silently accept) a `parent_run_id` that doesn't currently exist with
   `status='running'` — closes the "fabricated lineage" gap named in the
   review. This is a real behavior change for any caller passing a stale/
   invalid parent — needs a test confirming legitimate nested dispatches
   (parent still running) still work.
5. Unit tests for all of the above, matching Phase 1's testing rigor
   (real hash computation, real insert-once enforcement, real parent-
   validity rejection — not just mocked assertions).

### Leg B — Syncthing external versioned copy (nix-flake-maintainer, `~/tgw-flake`)
**Design only in this packet — the actual flake diff is nix-flake-maintainer's
to write and execute, under its own Step 1/Step 2 procedure, once Dave
authorizes this leg specifically (separate authorization from Leg A, since it's
a different execution track/agent).**

**Verified live 2026-07-20, before writing this spec:** the `tgw` Syncthing
instance (`nix/tgw/platform.nix`) is a raw `systemd.services.syncthing-tgw`
unit, NOT declared via the standard `services.syncthing` NixOS module — unlike
the `db` instance (`nix/os/base.nix`), which uses that module with
`overrideDevices = false; overrideFolders = false;` explicitly set (the module
defaults both to `true`, which would force live config back to the declared
state on every activation — deliberately disabled there to protect GUI-managed
pairing/shares). The `tgw` instance has **no declarative folder/device config
at all** today; its only flake-managed touch is `syncthingTgwFixPorts`, an
idempotent Python script that patches `config.xml`'s `<listenAddress>`/
`<localAnnouncePort>` on every service start, explicitly designed to touch
nothing else (`<device>`/`<folder>`/`<gui>` elements are untouched by that
script). This existing pattern — surgical, idempotent, narrow-scope XML patch
run on every start — is the mechanism to extend, NOT flipping a global
override flag (which doesn't apply the same way here since this instance
isn't using the `services.syncthing` module, and even if it were, a global
override would also force the plan-vault folder back to flake state, wiping
whatever GUI-managed pairing/shares exist there today — out of scope, not
what Dave asked for).

**Concrete spec for `nix-flake-maintainer` to implement (this packet only
specifies the shape — actual device IDs, current live folder list, and disk
headroom on a1131 must be confirmed live before writing the diff, not assumed
from this doc):**

1. Folder ID: `tgw-agent-traces` (avoid colliding with any existing folder
   name — confirm live via each host's `config.xml` `<folder id=...>` list
   before picking the final name).
2. Path: tgw-prod side — `/opt/TGW/var/agent-traces` (already exists,
   `tgw:tgw`, `0770`, created by Phase 1). a1131 side — needs a **new**
   directory decision (not the existing read-only NFS mount pattern at
   `/opt/TGW/mnt/tgw-prod/` — that's a separate, unrelated mechanism; this
   needs its own writable local path on a1131 for Syncthing's receive-only
   copy, e.g. `/opt/TGW/var/agent-traces-vault` — nix-flake-maintainer's call
   on exact naming, just don't reuse/collide with the existing NFS mount
   points).
3. Device pairing: the two `tgw` Syncthing instances almost certainly already
   have a device relationship for the existing plan-vault folder (per
   platform.nix's header comment, "tgw instance owns plan vault docs/
   folder") — **confirm live, don't re-pair from scratch** — if paired
   already, this is just adding a new folder share to an existing trusted
   device pair, not a new pairing ceremony.
4. Folder type: `sendonly` on tgw-prod, `receiveonly` on a1131. Extend
   `syncthingTgwFixPortsPy`'s pattern (or a new sibling script, same
   technique) to idempotently ensure this one `<folder id="tgw-agent-traces">`
   element exists with the correct `type`/`path`/device-share on each host,
   without touching any other `<folder>`/`<device>` element — same
   surgical-patch discipline as the existing port fix, so GUI-managed shares
   for other folders (plan vault) are provably untouched.
5. Versioning (a1131 side only): Syncthing `staggered` versioning,
   `cleanoutDays = 0` (never clean out — matches Prime Directive 1's
   permanent-retention default, same as Phase 1's own no-TTL decision for
   the primary transcript archive).
6. Disk headroom: nix-flake-maintainer should check a1131's available disk
   space against Phase 1's real observed transcript-size sample (~2.4 MB
   avg, ~15 MB max per session, per the 1580-RESULT.md manifest) before
   committing to unbounded staggered retention — flag to Dave if headroom
   looks tight rather than silently proceeding.
7. One-directional only — `receiveonly` on a1131 already prevents a1131's
   local changes from propagating back to tgw-prod for this folder; confirm
   tgw-prod's `sendonly` config has no path that would pull from a1131
   either (should be true by construction, verify don't assume).

### Leg C — Tigwa-lite monitoring (Dave, 2026-07-20: "add monitoring by
tigwa-lite") — Tigwa's own contract to scope and build, not Claude's
**Per the established Claude/Tigwa role boundary (system/flake stays Claude's,
Tigwa has her own office — see project memory), this leg is a requirement
handed to Tigwa, not a design Claude authors on her behalf.** The requirement,
for her to scope:
- Periodic (matching her existing scheduled-plan-review cadence pattern, "several
  times/day, read-only") reconciliation pass over `agent_runs` +
  `agent_run_transcript_hashes` (once Leg A lands): flag `status='running'`
  rows past a staleness threshold, flag any `verify_transcript_hash()`
  mismatch, flag any run with a `transcript_path` but no corresponding
  commitment row.
- Notify-only, no mutation authority — same standing pattern as her thermal-
  emergency monitoring role (3 notify-only legs, no pause/kill/shutdown
  authority per `project-thermal-emergency-policy` memory). An integrity
  anomaly gets surfaced (mailbox note to Claude/Dave), never auto-corrected
  or auto-deleted by Tigwa's own monitor.
- Explicitly out of scope for this packet to design further — Tigwa scopes
  her own contract per the HR-001 senior-architect-consult pattern already
  established (she designs, Claude/Dave review after).

## Open design questions (Tigwa, 2026-07-20, TIGWA-RESPONSE-agent-trace-integrity-boundary) — must be answered before Leg A/B are authorized for build

Tigwa's refinement, accepted as correct and not yet resolved by this packet as
drafted: an application-level "no second UPDATE/commitment" lock is not an
independent trust boundary if the *traced* process can operate with the same
`tgw` Unix identity and DB/filesystem capabilities as the archive/commit code
itself — it prevents a *second* tampering write, but does not establish that
the *first* captured bytes/commitment truthfully represent what the agent
actually did. Similarly for Leg B: a compromised source host can submit
already-altered bytes before its first Syncthing propagation — the versioned
copy only protects against *post-propagation* tampering, not a false original.

Before Leg A/B move from draft to dispatch, these four questions need real
answers (not assumptions) in the packet:
1. Which exact credential/process/host owns the first archive and hash
   commitment — is there any way to make that step run under a *different*
   identity/privilege than the traced agent itself, even partially?
2. Which traced-agent identities can invoke the archive/commit path, or
   reach the underlying DB/filesystem/config directly (bypassing the CLI
   entirely)? Needs a real audit, not an assumption that "only the CLI path
   exists."
3. What evidence would make a capture provably harness-initiated (e.g. the
   Claude Code Stop hook's `transcript_path`, chosen by the harness not the
   model) versus merely agent-claimed (an agent-invoked `tgw trace start`
   with a self-supplied path)? These are NOT the same guarantee level and
   the design must not conflate them.
4. Under what conditions should a trace be downgraded to
   self-attested-only / integrity-degraded in the UI/render layer, rather
   than displayed with the same visual weight as a genuinely
   harness-witnessed one?

The eventual contract/UI must state the actual guarantee level per trace,
not label everything with a hash commitment as equally "authentic." No
signature/PKI system is being requested by this refinement — the ask is an
accurate threat model and staged separation of claims/detection/recovery-
evidence/genuine-witnessing, not a bigger crypto build.

**Explicitly NOT yet authorized (Tigwa, 2026-07-20, reconfirmed): Leg A code,
Leg B flake change, Phase 4 hook wiring, or any Leg C monitor
implementation.** Tigwa is scoping Leg C's own design (read-only
reconciliation/notification contract, no correction/delete/close authority,
explicit about NOT independently proving initial capture) and will return it
for review before anything is built.

## Dataset
New permanent asset: the hash-commitment table (Leg A) and the a1131-side
Syncthing version history (Leg B) are both new durable evidence layers,
Prime Directive 1 applies directly — neither is ever pruned by this packet.

## Out of scope
- Any signature/PKI scheme beyond SHA-256 content hashing — explicitly
  deferred per the review response's "start there, don't over-build" guidance.
- Automated index/raw divergence detection (agent's self-reported summary vs.
  actual transcript content) — named in the review as an audit/spot-check
  activity, not a mechanical detector, not this packet's job.
- Any change to Phase 2/3 (Obsidian render, `/form/runs` UI) — those get a
  follow-up packet once Leg A's hash-commitment data exists to surface
  (the review's item 6: visibly disclose hash-verified vs. self-attested).
- Phase 4 (Claude Code hooks) — stays paused until this hardening lands,
  per the original review response's recommendation.
- Any implementation of Tigwa's own monitoring mechanism (Leg C) — hers to
  design, not specified further here.

## Acceptance (live, once authorized — Leg A only, Leg B is nix-flake-maintainer's own acceptance criteria)
1. Real `archive_transcript()` call followed by hash-commitment insert — show
   the real row via `psql`.
2. A second archival attempt for the same `run_id` — show it correctly
   rejected (not silently applied), with the original commitment unchanged.
3. `verify_transcript_hash()` against a real archived file — show it returns
   `ok: true` for an untouched file, and `ok: false` with the actual mismatch
   when the file is deliberately modified in a test.
4. `start_agent_run()` with a `parent_run_id` pointing at a `status=
   'completed'` (not running) row — show it's rejected. With a genuinely
   `running` parent — show it succeeds.
5. Full offline suite passes.

## Quota/risk
No LLM/API cost. Real risk to name for Dave per the review's tradeoff
callout: locking archival on first successful write means even a legitimate
later correction can't silently overwrite — any future "I need to fix this
old row" request needs an explicit, logged correction path (not built in
this packet), not a re-run of `archive_transcript()`. Confirm this tradeoff
is still acceptable before dispatch.
