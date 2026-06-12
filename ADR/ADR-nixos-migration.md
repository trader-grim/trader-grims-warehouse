# ADR: Migrate the TGW production host from MX Linux to NixOS

| | |
|---|---|
| **Status** | Accepted (decision by Dave, session 16, 2026-06-08 — "NixOS COMMITTED"). This ADR records and bounds that decision; it does not reopen it. |
| **Date** | 2026-06-10 |
| **Deciders** | Dave (operator/owner). Drafted by Claude (lead-architect session). |
| **Related** | PP-NIXOS-001, PP-DEPLOY-001 (mutually exclusive end-state, retained as rollback), PP-BACKUP-001, PP-PORTABLE-CATALOG-001, `docs/plans/PLAN-nixos-migration.md` (execution plan) |

## Context

TGW runs on a single MX Linux (Debian) host: PostgreSQL 17 work ledger, 18 systemd
template worker units (`tgw-worker@<queue>`), `tgw-http` (FastAPI :7373), Ollama, an
inotify+rsync backup watcher, and the canonical filesystem item store under `/opt/TGW`
(see `docs/architecture/overview.md`). The platform is healthy, but the OS layer is the
weakest part of the disaster-recovery story:

- DR on MX is "Debian plus scripts we write ourselves." The bootable-ISO path
  (MX Snapshot) is GUI-centric, coarse, and not infrastructure-as-code.
- There is no fine-grained OS rollback: a bad package upgrade or config change is
  recovered by hand or by full reimage.
- System configuration (installed packages, systemd units, postgres setup, permissions
  policy) lives partly in the repo (`etc/`, `systemd/`, `scripts/`) and partly as
  hand-applied host state — the two have already drifted (units installed only in
  `/etc/systemd/system/`, root-owned config files flagged by the permissions audit).
- The business goal is a reproducible "warehouse OS" (tgwOS): any node — production
  server, portable-catalog client, spare intake machine — buildable from declared
  config, with the recovery equation
  **NixOS flake + site-config GitHub repo + ItemData restore = full system rebuild**.

A commissioned analysis (Perplexity, session 16) concluded NixOS is architecturally
superior for this DR model, with two known rough edges: PostgreSQL WAL-recovery vs
`ExecStartPost` hooks, and Python packaging requiring a flake strategy. Dave's
background (8 years Gentoo, LFS, custom OSes) makes the learning curve acceptable.

Initial artifacts already exist in-repo: `flake.nix` (buildPythonApplication package +
`nixosModules.tgw` + a `vm` host config) and `nix/tgw.nix` (tgw user, PostgreSQL,
worker fleet, tgw-http, tmpfiles-managed `/opt/TGW` tree). **Authored for VM
validation only — never built on the MX host, not yet validated.**

## Decision

1. **NixOS is the target operating system** — **certain for the production server,
   probable for client machines**. Two host tiers from one flake: the complete
   architecture (`bases/master.nix`) runs only on the master and possible future online
   failover servers; client machines get the portable-catalog profile
   (`bases/portable.nix` — satellite SQLite catalog + thumbnails + Syncthing, no
   PostgreSQL/workers/eBay secrets). The client tier never blocks the server migration.
2. **Migration is staged, not big-bang**: spare intake machine first (client mode,
   services not started) → VM-validated full stack → production cutover, per
   `docs/plans/PLAN-nixos-migration.md`.
3. **The MX restore image (PP-DEPLOY-001) is the rollback boundary**: a verified
   bootable MX ISO + PostgreSQL dump + rclone data backup is baked *before* cutover
   and retained until NixOS has run a clean shakedown period (≥2 weeks green).
4. **`/opt/TGW` remains the self-contained platform entity** on NixOS: app paths
   unchanged (the code hardcodes them via `config.py`), venv/nvm/npm under
   `/opt/TGW/`, no `~tgw` home-directory state. The OS is disposable; the tree +
   flake + secrets are the system.
5. **Packaging via `buildPythonApplication`** (explicit nixpkgs deps), not
   `poetry2nix`: no lockfile exists, the dependency set is small and entirely in
   nixpkgs. Revisit only if a pinned closure becomes necessary. Corollary:
   `pyproject.toml` must remain the single source of truth the flake mirrors
   (the current Pillow base-vs-extra divergence must be resolved before cutover).
6. **Non-secret site config moves to a private GitHub repo**; secrets remain
   filesystem-only (`/opt/TGW/secrets`, 0700/0600), restored out-of-band, never in
   the Nix store or any git repo.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Stay on MX Linux + custom DR scripting** | DR automation ceiling is reachable but everything must be built and maintained by hand; no atomic rollback; config drift already observed. The ISO path stays GUI-driven. Retained only as the rollback safety net. |
| **MX + Nix overlay** (Nix package manager on the Debian host) | Gives reproducible devShells but not declarative OS state, services, or generational rollback — solves the least important half of the problem. Viable interim, dead end as a target. |
| **Guix System** | Same design space as NixOS; smaller ecosystem, Guile Scheme dialect, no first-class equivalent of the nixpkgs module breadth TGW needs (postgres, syncthing, qtile). No offsetting advantage. |
| **Fedora Silverblue/Kinoite** (immutable rpm-ostree + containers) | Immutability without full declarativity: service/app stack would live in containers and compose files — a second configuration language, and the `/opt/TGW`-as-entity model fits worse. |
| **poetry2nix / lockfile-pinned packaging** (within NixOS) | No `poetry.lock` exists; introduces a toolchain for a six-dependency app. Deliberately deferred, not rejected forever — see Decision 5. |

## Consequences

**Positive**
- OS state becomes code-reviewed, version-controlled, atomically rollback-able
  (`nixos-rebuild switch --rollback`).
- DR collapses to the recovery equation; any node is cloneable; the spare intake
  machine and portable-catalog clients become cheap stamped artifacts.
- Parallel version testing (Python, PostgreSQL, Qtile) without touching the running
  system.

**Negative / costs**
- One-time migration risk concentrated at cutover (mitigated by the staged plan and
  MX rollback image).
- Known sharp edges to engineer around, all itemized in the plan: PostgreSQL
  **17→NixOS default 16** mismatch (must pin `postgresql_17`), WAL-recovery
  `ExecStartPost` gotcha, `python3Packages.mcp` availability on nixos-24.11, the live
  `tgw` uid (1001 — **decided: migrate below 1000 on MX before the rollback image is
  baked**; candidate uid/gid 900, verified free), and a backup-unit `ExecStart` pointing
  at a binary not built by this package. Worker unit naming is **settled: keep the
  `tgw-worker@<queue>` template form on NixOS** so `tgw restart-workers`, `tgwlogs`, and
  all docs work unchanged.
- The hardware available for testing cannot run most Ollama models — NixOS structure and
  restore mechanics are validated on it, but inference is validated only on production
  hardware at cutover (or on upgraded hardware if that lands first).
- The ledger schema (`queue/schema.sql`) has no declarative bootstrap on NixOS yet —
  `ensureDatabases` creates an empty DB; schema application must be added or
  documented.
- Ongoing: Nix language maintenance burden on one person; nixpkgs channel upgrades
  become a recurring chore.

**Neutral**
- `ebay_sku_migrate` (months-long background migration) pauses across the cutover
  window and resumes unchanged — the ledger and item tree carry its state.
- eBay OAuth is host-independent; the token survives the move provided **scopes are
  not touched** (standing rule).

## Execution

See `docs/plans/PLAN-nixos-migration.md` — phased plan with per-step test coverage,
verification, rollback, and monitoring updates. No production code changes are made
under this ADR itself.
