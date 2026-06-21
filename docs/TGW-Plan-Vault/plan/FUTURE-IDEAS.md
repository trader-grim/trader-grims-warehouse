# TGW Future Ideas Catalog

**Purpose:** Deferred and long-horizon concepts that have been evaluated and preserved but
are NOT active work items. Research, code samples, and full context are kept here so that
ideas survive context compaction and session boundaries.

**When to read:** Only at dedicated planning sessions, or when Dave explicitly asks to
review future ideas. Do NOT scan or process this file at routine session start.

**Process:**
1. A suggestion or idea arrives (SUGGESTIONS.md or inbox)
2. Evaluated: actionable now → master plan PP item; not yet → this file
3. SUGGESTIONS.md item is checked off with "→ FUTURE-IDEAS.md" annotation
4. At planning time: scan this file for ideas ready to promote to active PP items
5. When promoted: add PP item to master plan, remove entry here

---

## PP-CATIONIX-001 — CatioNIX: TGW Platform as Standalone AI Operational Safety Platform

**Also referred to as:** Catio  
**Filed:** 2026-06-20  
**Deferral trigger:** Revisit after PP-AIOPS-001 Phase 4 (litterbox worker) is complete and
the pattern is proven end-to-end on TGW. Phase 4 is the proof-of-concept for the core
differentiator.

### Concept

Extract the TGW base platform into a standalone, general-purpose AI operational safety
platform called **CatioNIX** (short: Catio).

**The key distinction from Sécurix:** Sécurix confines human government employees.
CatioNIX confines AI agents. The "users" of the platform are AI processes, not people.
Any system where AI agents take real-world actions (file writes, API calls, order
placement, code commits) benefits from this safety envelope.

Components that would form the extractable platform:
- **CatioNIX OS layer** — NixOS service topology: declarative, reproducible, immutable base.
  Already being built in `nix/os/`. TGW-agnostic. Would be the same for any application.
- **Agent user pattern** — service accounts as confined AI agents: `isSystemUser=true`,
  home under `/opt/<agent>`, no login shell, specific UID range, `createHome=false` (tmpfiles
  owns tree). Currently in `nix/tgw/users.nix`. Future: `catio.agents` module option.
- **PostgreSQL work ledger** — `state_machine` DB, `QueueWorker` base class, job lifecycle
- **NATS JetStream audit stream** — `ITEMDATA_MUTATIONS` + `QUEUE_TRANSITIONS` (PP-AIOPS-001)
- **QueueWorker base class** — thin worker pattern, queue-in / queue-out / dead-letter
- **Litterbox pattern** — auto-fix for INFO/WARN anomalies; queue CRITICAL for operator ack
  with human-in-the-loop gating (PP-AIOPS-001 Phase 4)
- **Anomaly detection layer** — rule library over audit stream (PP-AIOPS-001 Phase 3)
- **Session isolation** — Btrfs CoW snapshot per agent session; bad sessions roll back in one
  command (PP-AIOPS-001 Phase 5)

### Differentiator

The crowded "AI safety" space focuses on model alignment and output filtering. CatioNIX
targets **operational safety**: the environment in which AI agents run, not the models
themselves. Key properties:
- Audit trail: every data change timestamped + attributed, observable after the fact
- Anomaly detection: bad patterns surface within seconds, not by operator discovery
- Human-in-the-loop gating: CRITICAL anomalies require operator ack before proceeding
- Automated remediation with escalation: litterbox auto-fixes known-safe patterns;
  unknown patterns escalate rather than guess
- Session isolation: bad agent sessions roll back in one command

TGW is already building all of this for itself. CatioNIX is what it looks like when
the TGW-specific parts are extracted and the platform is offered generically.

### Current module structure (layer separation progress)

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

**Separation test applied to `nix/os/base.nix`:** As of 2026-06-21, cleaned out TGW-specific
packages that had leaked in (`ffmpeg`, `imagemagick`, `exiftool`, `chafa`, `gh`, `ydotool`,
`thefuck`) and moved them to `nix/tgw/platform.nix`. CatioNIX base now passes the test: it
would work identically on a host running a different application.

**Future abstraction (`catio.agents` option):** When CatioNIX is separated as its own project,
`nix/tgw/users.nix` becomes the model for how any application declares its agent users:
```nix
# Future CatioNIX module option (not yet built)
catio.agents.tgw = {
  uid  = 900;
  home = "/opt/TGW";
  description = "Trader Grim's Warehouse service account";
};
```
The current manual declaration in `nix/tgw/users.nix` is already the right shape; the
abstraction is added without restructuring when separation happens.

### Related research

**Sécurix (DINUM / French government):** A NixOS-based hardened OS for confining users.
Directly relevant as architecture reference — adapt for AI agents as the confined entities.
- Open source: `github.com/cloud-gouv/securix`
- Key properties: declarative immutability (state defined in Nix → no config drift),
  TPM2 + LUKS FIDO2 hardware interlocking, Secure Boot with custom-keyed authority,
  instant reinstantiation when state diverges from baseline
- **Bureautix** shows how to fork and re-key for an alternate authoritative entity —
  same pattern CatioNIX would use to let other operators key their own deployments
- Architecture for AI agent confinement Dave noted:
  ```
  [ AI Agent Action ] → Modifies Files / Runs Malware → [ Local Ephemeral State ]
                                                              │
                                                 (Reboot / Agent Reset)
                                                              ▼
  [ Pure NixOS Baseline ] ◄═══ Cryptographic Lock ═══ [ Hardware TPM2 / Key ]
  ```
- Full research: `docs/TGW-Plan-Vault/inbox/archive/20260620T092933-securix-borgbackup.md`

### Relationship to current PP items

- **PP-NIXOS-001**: Builds the CatioNIX OS layer (`nix/os/`). Every session on this is
  progress toward a clean CatioNIX separation.
- **PP-AIOPS-001**: Builds the audit stream + litterbox — the platform's core safety
  components. Phase 4 (litterbox) is the concrete proof that the pattern is extractable.
- **TGW = first CatioNIX application**: `nix/tgw/` declares TGW as one implementation.

### Promotion criteria

Ready to promote to active PP item when:
- [ ] PP-AIOPS-001 Phase 4 (litterbox) is complete and proven on TGW
- [ ] PP-NIXOS-001 migration is stable on production
- [ ] Dave decides to pursue CatioNIX as a separate product/project

---

## Alt-text on all item photos

**Filed:** 2026-06-17  
**Suggestion text:** "We should add an option to add alt-text to additional photos, or
maybe even just put it on all of them. Books benefit a lot from back cover, table of
contents, copyright page."

**Context:** Current `ai_identify` worker generates alt-text/vision enrichment for the
primary photo only. Secondary photos (back cover, detail shots, copyright page for books)
carry significant product information that could improve listings.

**Deferral reason:** Vision pipeline is in flux (Google Vision confirmed fast+good
2026-06-13; Anthropic direct key pending). Design the multi-photo pass after the single-
photo pipeline is stable and the model routing is settled (PP-MULTIMODEL-001).

**Promotion criteria:**
- [ ] PP-MULTIMODEL-001 model router settled
- [ ] Single-photo pipeline stable on production
- [ ] Cost-per-item data available to estimate multi-photo pass cost
