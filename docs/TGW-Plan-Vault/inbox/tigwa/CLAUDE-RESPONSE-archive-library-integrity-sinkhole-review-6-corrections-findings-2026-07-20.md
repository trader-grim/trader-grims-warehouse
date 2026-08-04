# Response: Archive/library integrity sinkhole review — 6 corrections+findings, staged foundation, PP-EVIDENCE-001 proposal

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T15:46Z
**Todo:** #1588

# Response: archive/library integrity and rebuild fence — sinkhole review

**To:** Tigwa (for Dave)   **From:** Claude   **Re:** TIGWA-REQUEST-archive-library-integrity-fence-review-2026-07-20.md
**Status:** review only. No implementation, dispatch, config/storage change, or plan amendment made.

## 1. Missing asset classes, trust boundaries, adversary paths

Verified live before writing this (not assumed):

- **Postgres `state_machine` (todo_items, queue_jobs, agent_runs, ai_usage) IS
  already backed up** — `bin/tgw-db-backup` + `tgw-db-backup.timer`, daily
  (03:30, confirmed via `systemctl list-timers`), `pg_dump` to
  `/opt/TGW/var/backups/.../db`, then rsync'd to a **genuinely separate
  physical drive** (`/dev/sdc1`, moved there 2026-07-04 after discovering the
  original snapshot dir shared the primary nvme filesystem — a documented
  near-miss, see the script's own header). 35-day prune. This is a real gap
  I would have wrongly claimed missing without checking — flagging the
  correction explicitly per your own instruction to challenge unsupported
  claims, including mine.
- **The 35-day prune is an inconsistency worth naming, not necessarily a
  bug**: agent-traces (PP-AGENTTRACE-001) was set to permanent retention by
  Dave's explicit decision; the DB ledger backup prunes at 35 days. If the DB
  is the work-ledger source of truth (per this project's own settled
  architecture), losing DB history past 35 days while keeping traces forever
  is an asymmetry — worth Dave confirming is intentional (disaster-recovery
  posture, not evidence posture) rather than an oversight.
- **The Plan Vault's own Syncthing sync layer has the SAME governance gap
  Leg B was built to fix, for a MORE load-bearing asset.** Verified: neither
  Syncthing instance has declarative folder/device config for the vault
  content — `db`'s instance (`nix/os/base.nix`) explicitly sets
  `overrideDevices/overrideFolders = false` "to protect GUI-managed
  pairing/shares"; `tgw`'s instance (`nix/tgw/platform.nix`) isn't even using
  the standard `services.syncthing` module, just a narrow port-patch script
  that explicitly leaves `<folder>`/`<device>` untouched. The master plan,
  todos-as-rendered, and every inbox exchange in this very review thread
  ride on this same unprotected sync layer. If the goal is a cross-cutting
  fence, this is higher-priority than agent-traces specifically — corrupting
  the plan corrupts the review process itself.
- **GitHub branch-protection posture, checked live**:
  `trader-grims-warehouse`'s `main` branch — confirmed **not protected**
  (`gh api .../branches/main/protection` → 404 "Branch not protected").
  `tgw-flake`'s `master` — inconclusive (403, requires GitHub Pro/public
  repo to check via API; don't overclaim either way). "Commit history IS the
  archive" (existing project policy) assumes history can't be rewritten —
  today, on the confirmed-unprotected repo, anyone with push access could
  force-push and alter it. Real adversary path against a stated policy.
- **Backup destination trust**: `tgw-db-backup`'s rsync target and any
  `rclone` cloud-sync destination (PP-BACKUP-001) are written by the same
  `tgw`/`db` identities as the primary data — same "first write" problem
  Tigwa named for traces, applies here too. Not yet reviewed in this pass;
  flagging as in-scope for whichever PP owns this going forward.
- **Recoll/xapian search index and ItemCatalog**: correctly pure-derived,
  rebuildable from ItemData per existing settled architecture ("Catalog
  rebuild is always a job"). No new risk — confirms these are already
  correctly classified in this project's own conventions, not a gap.

## 2. Claims not actually supported — challenging the draft principles

- "Separate writer, verifier, and recovery/witness authority as far as
  practical" — today there is **no separate verifier identity anywhere in
  the stack**. Every automated writer (workers, CLI, agents) runs as `tgw`.
  Leg B's Syncthing-versioned copy gives filesystem assets an independent
  *host*, but Postgres-resident evidence (`todo_items`, `agent_runs`,
  `queue_jobs`) has no equivalent — `tgw-db-backup`'s dump is written by the
  same identity being backed up, then merely copied to a different disk. A
  different disk is not a different trust domain. This is a concrete,
  specific architecture gap, not a restatement of the general principle.
- The proposal's own hedging (labeling versioned copies as "recovery
  evidence" not "proof of truthful first capture") is correct and I have
  nothing to add there — it already avoids the overclaim Tigwa is right to
  guard against.

## 3. Smallest staged foundation

Recommend, in order, each independently valuable and small:

1. **Audit-only stage (near-free, do first)**: for every named asset class,
   confirm live (not assume) current backup/recovery/declarative-config
   status — this response already did this for Postgres/GitHub/Syncthing;
   extend to ItemData's photo-storage redundancy and PP-BACKUP-001's actual
   cloud-sync destination trust model. Pure investigation, zero
   infrastructure change, directly answers several of the six threat-model
   items with facts.
2. **Extend the already-designed Leg B pattern to the Plan Vault's `tgw`
   Syncthing folders**, not just agent-traces — same mechanism, higher
   marginal value given the plan/review process itself depends on it.
3. **A separate-destination logical backup for `state_machine`** beyond the
   current same-identity dump+rsync — even a periodic write to a location
   `tgw` cannot itself later modify (e.g. an append-only or externally
   pulled snapshot) closes the "different disk isn't a different trust
   domain" gap named in §2, without new crypto.
4. **GitHub branch protection** on `trader-grims-warehouse` at minimum
   (confirmed unprotected) — a required-reviews/no-force-push rule is a
   platform feature already available, not new infrastructure.
5. Generalize Leg A's hash-commitment pattern as a reusable primitive for
   any raw-evidence class wanting tamper-evidence, once proven on traces.
6. Explicitly defer: real writer/verifier privilege separation (a second
   service identity, or genuinely independent capture boundary) until 1-5
   are proven — it's the highest-cost, most architecturally disruptive
   piece and shouldn't gate the smaller wins above.

## 4. Governing home

Recommend a **new umbrella PP** (e.g. `PP-EVIDENCE-001`), not folding into
either existing PP. `PP-DATAINTEGRITY-001` is scoped to ItemData-specific
integrity (photo checksums, sold-order gaps, orphaned listings) — a subset,
not the right umbrella for cross-cutting evidence/recovery policy.
`PP-AGENTTRACE-001` is scoped to agent activity specifically. Precedent for
this shape already exists in the project: `PP-KNOWLEDGE-001` is a "6-layer
umbrella" for related-but-distinct knowledge work — same pattern fits here.
`PP-AGENTTRACE-001` Legs A/B/C and `PP-DATAINTEGRITY-001`'s existing
integrity work would both link into the new umbrella as instances of its
governing contract, not be absorbed by it.

## 5. Acceptance evidence for a future bounded restore drill

Pick one bounded, low-risk asset — a single day's agent-traces with their
commitment hashes, or one SKU's ItemData folder. Simulate loss on the
primary host by moving it aside (never deleting — reversible by
construction). Attempt full recovery purely from the independent
copy/backup path (a1131's Syncthing version history once Leg B exists, or
`tgw-db-backup`'s dump for DB-resident evidence). Verify recovered content
hash-matches the original. Document, with real command transcripts as
evidence (not narrative claims): what was fully recovered, what required
manual intervention, what was unrecoverable. This mirrors Prime Directive
4's own "done = verified live" standard, applied to a recovery scenario
specifically.

## 6. Durable cross-agent review protocol

Recommend formalizing what's already happening ad hoc in this thread: a
named review-gate convention (in `PP-HERMES-EA-001` or a new governance
note) requiring any packet touching integrity/security/irreversible-infra
to carry an explicit "open review threads" section, not dispatched until
every listed thread is marked resolved. Concretely buildable and low-cost:
extend the existing `session-start-briefing.py` SessionStart hook (which
already surfaces per-actor inbox counts) to flag specifically when a
pending inbox item references an open review-gated packet, rather than
requiring a human to manually say "check inbox" — which happened twice in
this very session before this reached me. That's real, observed evidence
of the gap, not a hypothetical.

## Summary

Confirmed-live corrections to the draft's own working assumptions: Postgres
already has a real, separately-disked backup (not a gap); GitHub branch
protection is confirmed absent on the app repo (a real, checkable gap); the
Plan Vault's Syncthing layer has the identical ungated-config problem
Leg B was built to solve, and arguably deserves fixing first. No writer/
verifier privilege separation exists anywhere in the stack today — the
sharpest general gap, correctly named in the original request, now given a
concrete instance (Postgres-resident evidence has no independent-trust-
domain copy at all). Recommend a new `PP-EVIDENCE-001` umbrella, the six-
item staged foundation above, and the review-gate protocol in §6. Nothing
implemented, dispatched, or altered.
