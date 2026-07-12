# PP-CATIONIX-001 — CatioNIX: TGW Platform as Standalone AI Operational Safety Platform

**Also referred to as:** Catio
**Filed:** 2026-06-20 (as a FUTURE-IDEAS concept)
**PROMOTED to active PP: 2026-07-11**, by Dave's direct decision, ahead of its
own originally-stated promotion criteria (see "Promotion — advanced ahead of
schedule" below). Previously lived at `plan/FUTURE-IDEAS.md`; full prior
content preserved below, extended with this session's structural kickoff.

---

## Concept

Extract the TGW base platform into a standalone, general-purpose AI operational
safety platform called **CatioNIX** (short: Catio).

**The key distinction from Sécurix:** Sécurix confines human government
employees. CatioNIX confines AI agents. The "users" of the platform are AI
processes, not people. Any system where AI agents take real-world actions
(file writes, API calls, order placement, code commits) benefits from this
safety envelope.

Components that would form the extractable platform:
- **CatioNIX OS layer** — NixOS service topology: declarative, reproducible,
  immutable base. Already being built in `nix/os/`. TGW-agnostic. Would be the
  same for any application.
- **Agent user pattern** — service accounts as confined AI agents:
  `isSystemUser=true`, home under `/opt/<agent>`, no login shell, specific UID
  range, `createHome=false` (tmpfiles owns tree). Currently in
  `nix/tgw/users.nix`. Future: `catio.agents` module option.
- **PostgreSQL work ledger** — `state_machine` DB, `QueueWorker` base class,
  job lifecycle
- **NATS JetStream audit stream** — `ITEMDATA_MUTATIONS` + `QUEUE_TRANSITIONS`
  (PP-AIOPS-001) — **note (2026-07-11): this is a DIFFERENT NATS use than
  PP-EVENTD-001's clip-route transport** (which settled on PostgreSQL
  LISTEN/NOTIFY, see PP-KNOWLEDGE-001). This one is a durable, replayable
  mutation-audit CDC log, deliberately not the same problem as real-time
  clipboard/UI event routing. Don't conflate the two "NATS" decisions.
- **QueueWorker base class** — thin worker pattern, queue-in / queue-out /
  dead-letter
- **Litterbox pattern** — auto-fix for INFO/WARN anomalies; queue CRITICAL for
  operator ack with human-in-the-loop gating (PP-AIOPS-001 Phase 4)
- **Anomaly detection layer** — rule library over audit stream (PP-AIOPS-001
  Phase 3)
- **Session isolation** — Btrfs CoW snapshot per agent session; bad sessions
  roll back in one command (PP-AIOPS-001 Phase 5)

## Differentiator

The crowded "AI safety" space focuses on model alignment and output filtering.
CatioNIX targets **operational safety**: the environment in which AI agents
run, not the models themselves. Key properties:
- Audit trail: every data change timestamped + attributed, observable after
  the fact
- Anomaly detection: bad patterns surface within seconds, not by operator
  discovery
- Human-in-the-loop gating: CRITICAL anomalies require operator ack before
  proceeding
- Automated remediation with escalation: litterbox auto-fixes known-safe
  patterns; unknown patterns escalate rather than guess
- Session isolation: bad agent sessions roll back in one command

TGW is already building all of this for itself. CatioNIX is what it looks
like when the TGW-specific parts are extracted and the platform is offered
generically.

## Current module structure (layer separation progress)

The `nix/` tree is already structured with the CatioNIX/TGW boundary in mind:

```
nix/os/          ← CatioNIX layer (TGW-agnostic)
  base.nix         OS config any CatioNIX host would have (SSH, tailscale, syncthing, admin tools)
  users.nix        Human operator account (db, uid 1000) — NOT TGW-specific
  desktop.nix      Opt-in GUI layer (X11+Qtile, KDE Connect, bluetooth, desktop apps)

nix/tgw/         ← TGW application layer (CatioNIX implementation)
  users.nix        tgw service account (uid 900, isSystemUser) — the first CatioNIX "agent user"
  platform.nix     TGW tools + syncthing folders + tgw-rebuild alias
  desktop.nix      TGW Qtile config (extraPackages, config.py symlinks)
  usb-sync.nix     TGW install bundle → USB via Syncthing markerName
```

**Separation test applied to `nix/os/base.nix`:** As of 2026-06-21, cleaned
out TGW-specific packages that had leaked in (`ffmpeg`, `imagemagick`,
`exiftool`, `chafa`, `gh`, `ydotool`, `thefuck`) and moved them to
`nix/tgw/platform.nix`. CatioNIX base now passes the test: it would work
identically on a host running a different application.

**Future abstraction (`catio.agents` option):** When CatioNIX is separated as
its own project, `nix/tgw/users.nix` becomes the model for how any
application declares its agent users:
```nix
# Future CatioNIX module option (not yet built)
catio.agents.tgw = {
  uid  = 900;
  home = "/opt/TGW";
  description = "Trader Grim's Warehouse service account";
};
```
The current manual declaration in `nix/tgw/users.nix` is already the right
shape; the abstraction is added without restructuring when separation
happens.

## Related research

**Sécurix (DINUM / French government):** A NixOS-based hardened OS for
confining users. Directly relevant as architecture reference — adapt for AI
agents as the confined entities.
- Open source: `github.com/cloud-gouv/securix`
- Key properties: declarative immutability (state defined in Nix → no config
  drift), TPM2 + LUKS FIDO2 hardware interlocking, Secure Boot with
  custom-keyed authority, instant reinstantiation when state diverges from
  baseline
- **Bureautix** shows how to fork and re-key for an alternate authoritative
  entity — same pattern CatioNIX would use to let other operators key their
  own deployments
- Architecture for AI agent confinement Dave noted:
  ```
  [ AI Agent Action ] → Modifies Files / Runs Malware → [ Local Ephemeral State ]
                                                              │
                                                 (Reboot / Agent Reset)
                                                              ▼
  [ Pure NixOS Baseline ] ◄═══ Cryptographic Lock ═══ [ Hardware TPM2 / Key ]
  ```
- Full research: `docs/TGW-Plan-Vault/inbox/archive/20260620T092933-securix-borgbackup.md`
- See also [[project-securix-fence]] (memory) — the AI-worker-token refinement
  of this idea, see "Crypto-lock" section below.

## Relationship to current PP items

- **PP-NIXOS-001**: Builds the CatioNIX OS layer (`nix/os/`). Every session on
  this is progress toward a clean CatioNIX separation.
- **PP-AIOPS-001** (`plan/PP-AIOPS-001-cat-herding-platform.md`): Builds the
  audit stream + litterbox — the platform's core safety components. **This is
  the primary technical substrate for the "catio" structure itself** — far
  more thoroughly designed than a from-scratch build: 6 phases already
  spec'd (JetStream audit stream → queue outbox → anomaly detector →
  litterbox worker + MCP tools → Btrfs/nspawn session isolation → rollback +
  observability), with real architectural decisions already made and
  reasoned (nspawn over Docker/microVMs, JetStream-as-log not JetStream-as-
  lock-store, alternatives evaluated and rejected). **Do not re-derive this
  design — extend it.**
- **PP-HERMES-EA-001** (new, 2026-07-11): the "dev team" — Tigwa/Leotha
  personas and their apprenticeship model. These are the eventual "cats" that
  get put into the catio (PP-AIOPS-001's sandbox/isolation substrate) one at
  a time, once trained. See that doc for the persona design; this doc doesn't
  duplicate it.
- **TGW = first CatioNIX application**: `nix/tgw/` declares TGW as one
  implementation.

## Promotion — advanced ahead of schedule (Dave, 2026-07-11)

**Original promotion criteria (unmet):**
- [ ] PP-AIOPS-001 Phase 4 (litterbox) is complete and proven on TGW
- [ ] PP-NIXOS-001 migration is stable on production
- [ ] Dave decides to pursue CatioNIX as a separate product/project

**Dave promoted this PP directly this session, overriding the stated
criteria — this is a deliberate decision, not a silent contradiction.**
Verbatim: "This is the beginning of the real catio implementation. It puts
the structure in place for the final implementation... That doesn't change
much yet." I.e. promotion here means **structural planning starts now**, not
that the unmet criteria (litterbox complete, NixOS stable) are being skipped
— those still gate the actual PP-AIOPS-001 phases they gate. What's
promoted is the PLAN's status (from "future idea" to "tracked PP with active
Phase 1"), not a claim that the underlying build prerequisites are done.

---

## Phase 1 — Structure (2026-07-11 session)

Three simultaneous upgrades, per Dave's own framing — "a catio, dev team,
and Dave upgrade":

### Catio vocabulary (Dave, 2026-07-11 — use these terms consistently going forward)

- **Ferals** — unclaimed resources not yet part of the household: unused
  free/bundled capacity (see "The ferals" section below). Never entered the
  catio at all.
- **Tame cats** — Tigwa/Leotha and future trained workers, deliberately
  onboarded, apprenticing under supervision (PP-HERMES-EA-001).
- **Strays** — "reserved for trained cats that deviate" (Dave). A tame cat
  that goes off-script — hallucinates, breaks a schema constraint, produces
  an unexpected side-effect. **This is not new architecture — it's the
  Catio name for PP-AIOPS-001's existing anomaly-detector/litterbox
  states**: known-safe deviations auto-fix (litterbox), unknown/critical
  ones escalate and hold for operator ack, exactly matching the `on_stray`
  fallback language already present in the raw cat-herder research
  (`cat-harness.md`: "strays: a list of unpredictable side-effects the AI
  is allowed to trigger... deterministic fallbacks... route it back to a
  sanitization state rather than crashing the loop"). The vocabulary is
  new; the mechanism it names already exists.

### 1. Catio (the platform/confinement structure itself)
- **Technical substrate: PP-AIOPS-001** (see above) — audit stream,
  anomaly detection, litterbox auto-fix, session isolation. Already
  thoroughly designed; this session adds no new design here, just formally
  links it under the CatioNIX umbrella.
- **Cat-herder router/middleware** — research-informed refinement layered
  on PP-AIOPS-001's existing anomaly-detector/litterbox pattern: sits
  natively over the existing Postgres state machine (never a second control
  plane); sidecar `ai_jobs`/`ai_manifests` tables (not a `herding_manifest`
  bloating domain tables); tiered isolation (host-direct for low-risk LLM
  calls, PP-AIOPS-001's nspawn sandbox only for untrusted/tool-heavy jobs);
  short transactions (claim fast, execute outside the transaction, write
  result back). Paperclip (AI agent orchestrator) was evaluated as a
  structural blueprint ONLY, not adopted — "connector piece, not a
  barnacle." Research: `/home/db/Downloads/cat-harness.md`.
- **Crypto-lock (endgame, not this phase)** — a Securix-style cryptographic
  per-worker execution token: signed policy lockfile + short-TTL unlock
  artifact + worker attestation + audit trail. Software trust primitive, not
  a hardware YubiKey clone. This is [[project-securix-fence]] retargeted
  from human-operator YubiKey confinement to AI-worker confinement —
  concretely, it's a hardening layer ON TOP of PP-AIOPS-001's Phase 5/6
  sandbox commit/rollback flow (the unlock token would gate a session's
  ability to promote its snapshot to live). Not a separate PP — track as a
  future addendum to PP-AIOPS-001 Phase 5/6 when that phase is reached.

### 2. Dev team (the Hermes/Tigwa/Leotha AI workforce)
See **PP-HERMES-EA-001** (new doc) for the full persona design. Summary:
Tigwa (business-facing executor, eventual `pm_intake` replacement) and Leotha
(Dave-facing translator/ideation partner) are two personas of one Hermes
instance. Both are explicitly IN TRAINING — Tigwa learns by operating `tgw`
itself, supervised, before any autonomous authority; autonomy unlocks only
once the crypto-lock (above) exists. These are literally the "cats" that
eventually go into PP-AIOPS-001's isolation substrate, one at a time.

### 3. Dave (his own upgrade)
- **Concept 5's doctrine shift** (see CLAUDE.md's new doctrine section):
  Dave moves from diff-reader to spec/acceptance judge — "does it match the
  spec, does it do what we want it to."
- **Tools that change how Dave physically works**: PP-EVENTD-001's
  Radar/active-context system (Concept 3), justshoutit voice-operated
  listing (Concept 4), the camera app (PP-INTAKE-004, Concept 6).

## "The ferals" — underused resources already in hand (Dave, 2026-07-11)

**Concept, in Dave's own catio terms:** "the line of workers at Home Depot,
or the unemployment line" — in Catio terms, **the ferals**. All the
resources TGW already has access to but isn't putting to work: unclaimed
capacity roaming the property, not yet brought into the household. Distinct
from Tigwa/Leotha (trained cats being onboarded deliberately) — ferals are
just *sitting there*, already paid for or already granted, waiting to be
noticed and claimed.

**Named examples (not exhaustive — the point is the category, not this
list):**
- The **$300 Google API credit** (from paying for Google services already —
  exact source not yet pinned down, worth confirming).
- **Antigravity** (Google's cloud-run agent tool — see
  `reference-tooling-agents` memory: "AGY (CLI) ≠ Antigravity 2.0
  (cloud-run agents) — never conflate").
- The wider **Google ecosystem** bundled with whatever's already being
  paid for — NotebookLM named specifically, likely more.
- General pattern: "tons of stuff we get for free with what we buy
  anyway" — i.e. this is a genuine AUDIT category, not a one-time list.

**Why this belongs under CatioNIX specifically:** the ferals are exactly
the kind of resource the cat-herder harness (Concept 1's `ai_jobs`
sidecar + tiered isolation) is built to route work to opportunistically —
cheap/free capacity slotted in wherever it fits, the same instinct as the
staffing research's cheap-coordination/premium-escalation pattern, just
extended to genuinely-free bundled tools instead of only cheap-vs-premium
paid models.

**Status: concept captured, not yet audited.** No inventory has been taken
— this session names the category and the instinct ("we should try to take
advantage of it"), doesn't yet enumerate what's actually available or how
much of it is usable. Real next step is an audit pass: what's bundled with
what's already paid for, what's actually usable for TGW work (vs. just
technically available), and where each fits in the cat-herder's routing
(interactive tooling, like ChatGPT Plus above, vs. worker-eligible).

## Sequencing — explicitly unchanged (Dave, 2026-07-11)

"That doesn't change much yet." [[project-catio-sequencing]]'s existing
principle stands: **stabilize TGW's core first** (the master plan's R1
critical-path track keeps running exactly as before); cats go into the catio
**one at a time**, not all at once, once Tigwa's apprenticeship proves out;
the cage (crypto-lock/confinement) comes **last**. This phase lays structure
only — a jump to full deployment is explicitly not what's happening now.

## Cross-links
- PP-AIOPS-001 (`plan/PP-AIOPS-001-cat-herding-platform.md`) — the technical
  substrate.
- PP-HERMES-EA-001 (new) — the persona/apprenticeship design.
- PP-EVENTD-001, PP-INTAKE-004 — the North-Star-facing tools built alongside
  (Concepts 3/4/6), not gated behind this phase.
- [[project-catio-sequencing]], [[project-securix-fence]] (memory).
