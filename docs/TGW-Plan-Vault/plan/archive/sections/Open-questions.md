## Open questions
- Per-queue worker counts (start: 1 each; serialize AI work in Phase 5)
- Where does the Ollama lock live — in the job manager worker or a Postgres advisory lock? (Phase 5 decision)
- PP-ADD-001 conflict resolution policy: last-write-wins vs. manual review (decide before Phase 6 dev)
- Thumbnail cache: install Pillow (`pip install Pillow` or `pip install trader-grims-warehouse[thumbnails]`) then run `tgw build-thumbnails`
- Item JSON globals block: should offer-invariant properties (condition class, preferred category, weight, shipping intent) have a dedicated `globals` block, or stay as top-level fields? Analyze before implementing — see PP-GLOBALS-001

### PP-REMOTE-001 — Remote Full Capability (SSH / Tailscale / tmux)
- Install and configure Tailscale on master for secure remote access; add to account network
- tmux: persistent session layout for TGW ops (catalog pane, worker monitor, Claude Code pane)
- Verify `tgw-http` reachable over Tailscale for Flutter app on remote devices
- Verify macro dispatcher (`tgw-macro`) works over SSH — clipboard via OSC52 or tmux buffer fallback
- SSH hardening: key-only auth, `tgw` user access, sudoers scoped to needed ops only
- **Open question**: should Claude Code have its own dedicated system user (e.g. `claude`) with scoped
  permissions, separate from the `tgw` worker user? Relevant to sudoers design and audit trail clarity.
  Decision: make part of the PP-REMOTE-001 hardening pass.

### PP-DEPLOY-001 — MX Linux OS Image Integration

#### Context
The system runs MX Linux. `mx-slapshot` creates bootable, installable OS images; Dave has a
library of images going back many years. Goal: make TGW a first-class citizen of the OS image
so that `image + /opt/TGW` = complete, running system with zero manual setup.

#### Design goals
- TGW fully operational from a fresh image restore — no manual service enables, no path fixes
- `tgw` user moved to UID < 1000 (system user range); ensures UID survives across image restores
  and avoids conflicts with future desktop user accounts
- Long-term: discontinue direct interactive `tgw` user sessions; all operator interaction through
  `tgw-http`, CLI tools (`tgw ...`), and Claude Code running as a scoped user

#### Work items
- [ ] Identify all places UID/GID assumptions exist (file ownership in `/opt/TGW/`, secrets
  permissions, systemd `User=tgw`, crontabs if any)
- [ ] Plan UID migration: choose target UID (e.g. 999), usermod, chown sweep, test all services
- [ ] Document image snapshot procedure: what must be in `/opt/TGW/` vs what's in the image
- [ ] Add TGW service enables to image baseline (systemd preset or post-install hook)
- [ ] Test: fresh image restore + mount `/opt/TGW` → `tgw health` green with no intervention

#### Dependencies
- PP-REMOTE-001 (Tailscale) — remote access must survive UID change
- PP-SHELL-001 — tgw.source cleanup before baking into image

### PP-NIXOS-001 — NixOS Migration Evaluation

#### Motivation (session 9 analysis)
Debian's advantage is stability and ubiquity — the system is rock solid and dependencies are
well-understood. The trade-off is dependency lock-in and an outdated feature set (older kernel,
older Python, older packages). NixOS offers:
- **Parallel version deployment**: run the stable system unchanged while testing a newer version
  of any component (Python, Postgres, Qtile) in a separate Nix derivation — no risk to the
  running system
- **Atomic rollback**: if a change breaks something, `nixos-rebuild switch --rollback` restores
  the last good state in seconds
- **Disaster recovery**: the entire system configuration is a single file (`/etc/nixos/configuration.nix`);
  combined with `/opt/TGW` and a repo restore, a full system rebuild is automated
- **Reproducibility**: any node can be cloned to the exact same state from the config file

#### Perplexity research findings — PostgreSQL + Python + DR (session 16)
Comprehensive analysis commissioned from Perplexity (MX Linux vs NixOS for PostgreSQL-backed
state machine). Key conclusions:

**DR verdict: NixOS is architecturally superior for DR.**
- MX Linux: OS-level DR is "Debian + scripting you build yourself." LuckyBackup (rsync-based)
  and MX Snapshot (bootable ISO) are GUI-centric and not inherently infra-as-code.
- NixOS: entire OS config is version-controlled Nix files. DR = "restore Postgres base backup
  + WAL" + "nixos-rebuild from flake." Config is the single source of truth.

**PostgreSQL on NixOS:**
- First-class module (`services.postgresql`) — version, data path, config, initial DB/users all declared
- pgBackRest + WAL archiving modules available (some permission/UMask rough edges in defaults — overridable)
- ⚠ **Known gotcha**: WAL-recovery conflict — `ExecStartPost` hook tries `ALTER USER` while DB is
  in read-only recovery mode → systemd kills the service. Mitigation: disable the hook or add a
  recovery-mode guard. Solvable but non-obvious.

**Python on NixOS — updated strategy:**
- Flake-based devShells with `direnv`/`devenv` for auto-activation on `cd`
- Packaging: `poetry2nix` or `buildPythonPackage` for deps not in nixpkgs
- Full pattern: one flake defines devShells (dev) + app package + nixosConfigurations (prod systemd services)
- TGW's `pyproject.toml` is already flake-compatible — straightforward to wrap

**Given Dave's background (Gentoo 8 years, LFS, custom OSes):**
Perplexity's explicit recommendation: NixOS is learnable — a small change given the background.
The Nix language is just a new dialect. The functional/declarative constraints become features,
not friction.

**Alternatives assessed:**
- **Guix System** — same design space as NixOS but Guile Scheme syntax; can also overlay on MX
- **Fedora Silverblue/Kinoite** — immutable rpm-ostree base + containers for app stack; less fully declarative
- **"MX + Nix overlay"** — keep MX host, add Nix for reproducible devShells without full migration

#### Decision framework (updated)
| Factor | MX Linux (Debian) | NixOS |
|--------|--------|-------|
| OS-level rollback | MX Snapshot ISO (coarse) | Fine-grained generational rollbacks |
| PostgreSQL integration | Standard Debian; you write all scripts | First-class module; some edge cases |
| Backup tooling | LuckyBackup/Snapshot; GUI-centric | Define pgBackRest/WAL in Nix; fully automatable |
| DR automation ceiling | High — you build declarative layer | Very high — OS is infra-as-code |
| Python env mgmt | Standard pip/venv | ⚠ Needs flake + poetry2nix; solvable |
| Dependency freshness | ⚠ Older packages | ✅ Latest available |
| Learning curve | ✅ Familiar | Moderate for Dave (low given background) |
| MX Linux image compat | ✅ Natural | ❌ Incompatible with mx-slapshot (mutually exclusive) |

**PP-DEPLOY-001 (MX Linux image) and PP-NIXOS-001 are mutually exclusive end-states.**
Decision recommendation: NixOS, pending the Python flake prototype.

#### Work items

**Completed (session 17–18):**
- [x] `flake.nix` + `nix/tgw.nix` authored (Round 3 #27) — `buildPythonApplication`, per-queue worker services, PostgreSQL, tgw-http, tgw user
- [x] `PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` authored (Round 3 #28) — pre-snapshot checklist, ISO verify, full restore steps
- [x] NixOS committed as target OS (session 16 decision)

**Pending — operator / VM actions:**
- [ ] **Validate flake.nix in NixOS VM** — Dave builds + tests on a VM; note: `python3Packages.mcp` availability in nixos-24.11 is a watch item
- [ ] ⚠ Mitigate WAL recovery gotcha: `ExecStartPost` ALTER USER runs while DB is in read-only recovery → service killed. Add recovery-mode guard before production use.
- [ ] **Spare intake machine as first NixOS target** (session 18 decision): install NixOS on the spare intake support machine; configure as client/portable-catalog (services not started); gain tool familiarity without risk to the main machine. When proven: promote to tgwOS server or full replacement.

**Flake architecture requirements (session 18):**
- **Platform flake** (`flake.nix`) — `tgw` user + workers + PostgreSQL + `tgw-http`; already authored
- **venv / nvm / npm on `/opt/TGW/`** — move tgw user virtualenv (`/opt/TGW/.venvironments/tgw/`) and nvm/npm (`/opt/TGW/.nvm/`) out of `~tgw/` so `/opt/TGW` is a self-contained imageable entity with no home-dir dependencies; update flake `HOME` or env vars accordingly
- **Personal operator flake** (separate) — Firefox, KDE Plasma, personal apps; composable via NixOS `imports`; not part of the platform flake
- **Dependency source-of-truth unification (open, session 19)** — `flake.nix` declares `pillow` as a **base** runtime dep (lines 63 + devShell 119), but `pyproject.toml` only carries `Pillow>=10.0` in the **optional** extras (`thumbnails` + `dev`), not base deps. The CI commit (`3be0d85`) added Pillow to the `dev` extra so fingerprint tests run, but did **not** reconcile the base-vs-optional divergence. Before NixOS cutover, make one source authoritative — either promote Pillow to a base `pyproject` dependency (PP-VISION-001 fingerprint is now core, not optional) or drive the flake from `pyproject` extras via `poetry2nix`/lockstep so the two can't drift. Pick the former if fingerprinting is here to stay (recommended); the latter if extras-as-optional is the intended packaging contract.

**DR / bootstrap design (session 18):**
NixOS install must support two bootstrap modes:
1. **Fresh warehouse start** — empty `/opt/TGW`; workers spin up; first item intake begins immediately
2. **Adopt existing data** — `/opt/TGW` restored from backup; config applied; full pipeline resumes from last state

Recovery equation: `NixOS flake + site config GitHub repo + ItemData restore = full system rebuild`

**Site config in GitHub** — `tgw-api-config.json` and non-secret config in a private GitHub repo; NixOS flake fetches at build time; enables any node to self-configure without local copy.

**Google Drive DR** — rebuild kit (NixOS ISO pointer, site config repo URL, rclone restore script for ItemData) lives on Google Drive. Major disaster: boot NixOS ISO → pull config from GitHub → restore ItemData from Drive → `tgw health` green.

#### Syncthing NixOS deployment (session 19/20 — syncthing-nixos-nginx-research.md)

TGW runs a dedicated headless Syncthing instance (separate from personal user instances):

```nix
# In modules/bases/master.nix
services.syncthing = {
  enable = true;
  user = "tgw";
  dataDir = "/opt/TGW/sync";
  configDir = "/opt/TGW/.config/syncthing";
  guiAddress = "127.0.0.1:8385";
  settings.options.listenAddresses = [ "tcp://0.0.0.0:22001" ];
  settings.options.insecureSkipHostCheck = true;
};
```

Port allocation:
- TGW headless: 8385 (GUI/REST), 22001 (sync protocol)
- Regular user instances: 8384 (default), 22000 (default)

Nginx reverse proxy block (for remote GUI access):
```nginx
server {
  listen 8386;
  location / {
    proxy_pass http://127.0.0.1:8385;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
  }
}
```

Auto-TLS: systemd oneshot `Before=nginx.service` generates self-signed cert. Replace with
ACME when the machine has a DNS name.

GUI access from dev workstation: `ssh -L 9000:127.0.0.1:8385 tgw@server`

LTSP fat clients: per-hostname Syncthing config via symlink
`/opt/TGW/.config/syncthing/config.xml → /opt/TGW/.config/syncthing/config.d/<hostname>.xml`
(different folder mappings per machine role).

**Operator unblock steps for PP-PYIPC-001:**
- [ ] Install Syncthing on NixOS target; confirm REST accessible at 8385
- [ ] Generate API key in Syncthing GUI → save to `secrets_root/syncthing-api-key` (chmod 600)
- [ ] Add `"syncthing_api_key_path": "/opt/TGW/secrets/syncthing-api-key"` to `tgw-api-config.json`

#### Multi-tier flake architecture (session 19/20 — system-app-config-and-nixos-flake-design.md)

Recommended structure for the full TGW NixOS platform using `flake-parts`:

```
nix/
  modules/
    bases/
      master.nix        # PostgreSQL, tgw workers, tgw-http, backup, Syncthing
      portable.nix      # portable catalog client (no workers, read-only tgw-http)
    interfaces/
      cli.nix           # terminal tools: tmux, mc, tgw.source, bash completion
    graphical/
      tiled.nix         # Qtile (primary intake workstation)
      plasma.nix        # KDE Plasma 6 (general-purpose desktop)
      thin-client-rdp.nix  # lightweight RDP thin client session
    ai/
      compute-node.nix  # Ollama + GPU drivers; models on /var/lib/ollama/models
  hosts/
    production-server.nix   # master + cli + tiled (+ plasma optional)
    portable-laptop.nix     # portable + cli + plasma
  dev-env/
    flake.nix           # separate stacking flake; nix develop ./dev-env
```

LTSP fat clients: NFS `/nix/store` shared; model weights on NFS mount (not in initrd).
Thin clients: `thin-client-rdp.nix` module; lightweight sessions connecting to master.
Dev flake: separate from platform flake; `nix develop ./dev-env` does not require system rebuild.

**Round 5+ tasks:** Refactor `flake.nix` to match this structure; add `portable.nix` base;
add `ai/compute-node.nix`; wire Syncthing service into `master.nix`.

#### TGW Distribution Design (session 37 — nix-distro.md Google AI research conversation)

**Goal:** Make the NixOS-based TGW platform as simple to distribute as the current MX image,
without bootable USB sticks yet — just a stable, replicable config bundle.

**Three pillars:**

1. **Git for versioning** — flake repo is the single source of truth; all module changes go
   through git; GitHub private repo already live.

2. **Syncthing for distribution** — flake bundle lives at `/opt/TGW/sync/flake/` on each
   node; Syncthing pushes changes automatically. ⚠ **Git evaluation trap**: Nix ignores
   Syncthing-updated files unless git-tracked. Workaround: use the `path:` prefix:
   `sudo nixos-rebuild switch --flake path:/opt/TGW/sync/flake#<hostname>`
   This forces Nix to evaluate the raw directory state, bypassing git strictness.
   - Declarative device + folder config in Syncthing bootstrap module (see Syncthing NixOS
     deployment section above); nodes connect to site server immediately on first boot.
   - **Per-machine host-overlay pattern**: `builtins.pathExists ./host-overlay.nix` in
     `modules/core/default.nix`; drop a `host-overlay.nix` file to specialize a node without
     polluting the shared config. File doesn't need to be git-tracked.

3. **nix MCP specialist for maintenance** — Claude Code as orchestrator + Context7 MCP for
   live nixpkgs docs + `nixai` CLI tool (hardware detection, repo-to-derivation helpers) +
   **eval-and-fix loop**: AI drafts → `nix flake check` → capture error → rewrite → repeat.
   Use `flake-parts` to isolate module concerns (packages / devShells / NixOS configs) so
   the AI reasons about small, bounded modules rather than one giant attribute set.
   - `jailed-agents` (Bubblewrap sandbox) or `agent-sandbox.nix` for safe agent execution
   - System prompt guardrails for Nix: pure syntax, no legacy `nix-env`, `flake-parts`,
     named `systems`, all dev envs in `pkgs.mkShell`, validate with `nix flake check`.

**New tools / decisions from research (investigate for Stage 4):**

| Tool | Purpose | Status |
|------|---------|--------|
| **Disko** | Declarative Nix partitioning — **LVM for base+postgres+microvms, Btrfs for /opt/TGW data** (2026-06-22 decision; replaces Btrfs+NoCoW design); `tgw-prod-disko.nix` authored | tgw-test: done; tgw-prod: layout done, device/sizes set at cutover |
| **Home Manager** | Manage tgw user + Dave operator dotfiles declaratively (Qtile, Plasma6, Bash/Zsh/Fish) | Plan for Stage 4 |
| **agenix** | Age secrets via Nix; standalone `.txt` key (NOT SSH-based) at `/var/lib/agenix/key.txt`; `secrets/secrets.nix` matrix maps machine public keys to encrypted files | Plan for Stage 4 |
| **nixai** | Terminal TUI with hardware detection + derivation helpers; augments nix MCP specialist | Evaluate |
| **Hardware fingerprinting** | DMI product name + storage class → auto-select flake target; already planned in `tgw-install.sh` | In plan |

**PostgreSQL backup strategy confirmed:** `pg_dumpall` (logical dump). Database lives on
LVM+XFS LV (`/var/lib/postgresql`); dump goes to `/opt/TGW` Btrfs volume. No Btrfs send
or NoCoW complexity needed — the LVM+XFS design eliminates the CoW concern entirely.
Matches existing PP-BACKUP-001 design.

**Home Manager layer design (Layer 3: Users):**
```
modules/users/
  tgw/
    home.nix          # tgw service user — minimal; no interactive shell config
  operator/
    home.nix          # Dave: kitty, yazi, fastfetch, fish/bash/zsh config
    shells.nix        # bash/zsh/fish aliases, tgw.source, completions
```
`home-manager.nixosModules.home-manager` inline in `flake.nix` so one
`nixos-rebuild switch` updates OS + user dotfiles together.

**Stage 4 additions derived from this research:**
- [x] Disko: `tgw-test-disko.nix` (Btrfs) + `tgw-prod-disko.nix` (LVM+XFS+Btrfs) authored; wired into flake.nix (2026-06-22)
- [ ] Add `modules/implementation/secrets.nix` using agenix (standalone key design)
- [ ] Add `modules/users/` with Home Manager for tgw and operator
- [ ] Wire declarative Syncthing bootstrap into `modules/bases/master.nix`
- [ ] Update installer to use `path:` prefix and hardware fingerprint → auto-select host profile

### PP-CAPTURE-001 — Idea and Task Capture Pipeline

#### Problem
Good ideas and small tasks surface mid-session, mid-work, or on a second device. The current
path — drop a `.md` file in `inbox/` or run `tgw suggest "..."` — works but isn't ergonomically
the first thing you reach for. The risk is ideas escaping into conversation chat where they
don't persist to the next session.

#### Proposal
Make `tgw suggest` the canonical back-channel for every idea, small task, and BTW thought —
instead of saying it as a parenthetical in conversation. Advantages:
- Auto-processed by PM-intake at the start of every session
- Creates an audit trail (timestamped, in git via plan updates)
- Survives context resets and context compression
- Works from the macroboard (`x` key → `tgw suggest`)

#### "Quiet queue" trigger
When no workers have active jobs (queue depth = 0 across all queues), surface pending
suggestions or operator TODOs via a `tgw status` or notification. This bridges the gap
between "workers finished" and "operator knows what to do next."

#### Implementation ideas
- `tgw suggest` already works — it's about adoption as a habit
- Consider alias `tgw note "..."` or `tgw btw "..."` for mid-session use (shorter to type)
- Quiet-queue hook: `ebay_price_reducer`/`ebay_sync` could emit a notification when
  queue is empty — or a lightweight cron `tgw quiet-check` that fires daily
- CLAUDE.md session protocol already picks up `tgw suggest` entries via SUGGESTIONS.md scan
- **Suggestion editor** (session 9 addition): lightweight tool to review, annotate, edit, or delete
  entries from SUGGESTIONS.md before PM-intake processes them. Use case: catching duplicates or
  clarifying ambiguous entries before they get embedded in the plan. Implementation: `tgw suggest-edit`
  opens a filterable list (fzf or TUI); edit → save → marks entry with a status tag.

#### Status
`tgw suggest` / `tgw note` / `tgw btw` — ✅ working. Suggestion editor — planned (Track 1 XS).

### PP-SHELL-001 — Shell Environment Cleanup (tgw.source / tgw-dev.source)
- Audit `tgw.source`: replace functions that duplicate `tgw` CLI subcommands with one-line wrappers or remove; keep only short-name convenience aliases worth keeping
- Audit `tgw-dev.source`: migrate anything useful to `tgw.source`; retire the dev file
- Rule of thumb: if it's not interactive/session-specific, it belongs as a `pyproject.toml` console script in the package, not a bash alias
- Outcome: `tgw.source` is a thin convenience layer on the `tgw` CLI; no parallel API surviving alongside it

**Tier 3 open items (2026-06-11):**
- **Help grouping** — `tgw --help` now lists ~65 subcommands; group them by function category
  using argparse `parents` or a custom formatter. Suggested groups: Read/Search, Write/Update,
  Pipeline, eBay, Context, Catalog/Build, Ops/Admin. Reference: argparse `add_argument_group`
  on the top-level parser or a manually-formatted epilog. Add to Track 1 when PP-SHELL-001
  Tier 3 work resumes.
- **`requeue` rename** — currently only re-queues `ai_identify` despite generic name; rename
  to `requeue-identify` or make it queue-agnostic before Tier 3 closes.

### PP-CONTEXT-001 ✅ DONE 2026-06-11 — Current-item context: `tgwset` replacement
Dave: the legacy `tgw set` (shell `tgwset` in `tgw.source`) sets an item persistently
systemwide so multiple operations can target it. It works but is fragile — needs a new
strategy, likely replaced, and the replacement must be **idempotent**.

**Dave note (2026-06-11):** Keep `tgw set` — most use cases are covered by new development
(photo display, editing live eBay listings via JSON data, uploading photos to eBay quickly)
but the feature is simple and useful in certain circumstances. **Do not remove** until the
replacement is feature-complete and field-tested.

**How the legacy mechanism works (audited session 20):**
- `tgwset()` does `rm` + `ln -sf` of three symlinks: `/opt/TGW/CurrentItem` →
  `ItemData/<SKU>/`, `/opt/TGW/CurrentItem.json` → the item JSON,
  `/opt/TGW/CurrentLocation` → catalog location dir
- `getsku()` resolves the context by `realpath` on the symlink; falls back to legacy
  `searchcatalog.json` via jq for eBay-ID→SKU and 18-char-prefix matching

**Why it's fragile:** non-atomic remove-then-link (a reader between the `rm` and `ln`
sees no context); constructs ItemData paths outside the fence; depends on the legacy
search catalog file; silent fallback to the Queue dir when the SKU doesn't validate;
no `{ok,...}` output contract; only one global context with no record of who set it.

**Design direction (discuss before building):**
- Promote to a first-class fence concept: `tgw context set <selector>` / `tgw context get`
  / `tgw context clear`, full `{ok,...}` contract, selector resolution via `resolve()`
- Idempotent by construction: setting the already-current SKU is a success no-op;
  `clear` on empty context is a success no-op; set = single atomic replace
  (`ln -sfn` via temp+rename, or sidestep symlinks entirely with a small state file
  `runtime/state/current-item.json` {sku, set_at, set_by})
- Keep `/opt/TGW/CurrentItem` symlinks as a **derived compatibility view** maintained
  by the same command (existing MC/shell consumers keep working during transition)
- Scope question for Dave: one systemwide context (current behavior) vs named/per-surface
  contexts (e.g. camera station vs desk) — systemwide is the stated requirement
- Related: PP-SHELL-001 (the shell layer keeps thin wrappers calling `tgw context`),
  PP-CLIP-001 (clipboard intake reads the context)

### PP-IFDIR-001 — Interface File Organization
- Currently: MC configs live at `/opt/TGW/mc/` (outside repo); keyd at `etc/keyd/`; no unified structure
- Goal: move all operator interface configs into repo under `etc/interfaces/mc/`, `etc/interfaces/keyd/`, etc.; update install scripts to deploy from there
- Makes repo the single source of truth for all interface configuration; simplifies new-node bootstrap

### PP-STORE-001 — eBay Store Category Support
- Add `store_category_id` to `draft_listing`; allow items to be filed into eBay store sections
- Store category list queried once via Trading API `GetStore` and cached (store categories rarely change)
- Default store category configurable per eBay category in `tgw-api-config.json`
- Wired into `ebay_stage` and `ebay_publish` offer bodies

### PP-GLOBALS-001 — Item JSON Globals Metadata ✅ ANALYSIS DONE (2026-06-07)

**Finding: no `globals` block needed.** Top-level fields already are the globals layer.

**Offer-invariant field audit:**

| Property | Current home | Assessment |
|---|---|---|
| `condition` (human string) | top-level | ✓ correct; source of truth |
| `condition_id/enum/label` | `draft_listing.*` | ✓ correct; eBay-derived copies |
| `ebay_category_id` / `ebay_category_name` | top-level | ✓ correct |
| `category_group` / `size_class` | top-level (via set-template) | ✓ correct |
| `upc` | top-level | ✓ correct |
| `format` (FIXED_PRICE) | TGW-wide constant | not worth storing per-item |
| `quantity` (always 1) | TGW-wide constant | not worth storing per-item |
| `marketplaceId` / `shipToLocations` | account-wide constant | not worth storing per-item |
| Policy IDs (fulfillment/payment/return) | config + category override | correct; never per-item |
| `merchantLocationKey` | account-wide, from config | correct; never per-item |
| **`weight_oz`** | **missing entirely** | **⬅ add this** |

**Action — add `weight_oz` (top-level, float, nullable):**
- Written by: PP-INTAKE-001 Phase 2 web form; PP-FULFILLMENT-001 USB scale; operator
- Used by: `ebay_draft` (item specifics for shipping weight); `size_class` derivation when not set by template; shipping label generation (PP-FULFILLMENT-001)
- Additive — safe to add now without waiting for Pass 3 schema freeze
- Do NOT add until PP-INTAKE-001 Phase 2 (the write path) is designed; schema freeze applies to renames/deletes only

**No schema restructuring needed.** The condition duplication between top-level and `draft_listing` is legitimate (top-level = source of truth; draft_listing = eBay API-formatted copies). Adding `globals` indirection would require every worker to change `doc.get('condition')` → `doc.get('globals', {}).get('condition')` with no benefit.

- Depends on: PP-ADD-005 (SKU normalization) + Pass 3 data scrub (field schema freeze) — for any renames; `weight_oz` addition is exempt (additive)

### PP-LOOKUP-001 — Product Data Enrichment ✅ ALL TIER 1 DONE (2026-06-05)

`apis/lookup/` package; `lookup_product()` dispatcher; results in `product_lookup` key (30-day cache).
Integrated into `ai_identify` (runs before Ollama) and `tgw lookup <SKU>` CLI.

**Tier 1 sources (all implemented):**
- `upcitemdb` (primary, 698M barcodes, 100/day free) → `go_upc` fallback (1B items)
- `open_library` (books/ISBN, no auth) · `discogs` (music, needs credential) · `igdb` (games, Twitch OAuth)
- `justtcg` (trading cards, no auth) · `open_food_facts` (food/household, no auth)

**Credential status (2026-06-05):**
- `secrets_root/igdb-credentials.json` — ⏳ Twitch app registered but key not yet visible in portal; check back
- `secrets_root/discogs-credentials.json` — ✅ Done
- `secrets_root/go-upc-credentials.json` — ❌ No free tier available; skip; Go-UPC is paid-only
- `secrets_root/upcitemdb-credentials.json` — ✅ Not needed; free tier (100/day) works keyless; code already handles this

**Integration details (PERPLEXITY-004, 2026-06-05):**
- **Discogs**: 60 req/min authenticated; personal token for automation; barcode lookup:
  `GET /database/search?barcode=<UPC>&type=release` (JSON array of releases); must send `User-Agent` header;
  30-day cache TTL recommended; search endpoint requires auth even for reads
- **IGDB**: Twitch app registration is instant; 4 req/sec / 8 concurrent max; queries use Apicalypse POST:
  `POST /v4/games` body: `search "Title"; fields id,name,slug,first_release_date; limit 10;`
  OAuth token via `POST id.twitch.tv/oauth2/token?grant_type=client_credentials`; 14–30 day cache TTL
- **Go-UPC**: Dev tier = 5,000 lookups/month, 2 req/sec; bearer token auth;
  `GET /api/v1/code/<barcode>?key=<key>`; 90–180 day cache; dedupe aggressively; monthly quota is hard stop

**Tier 2 (decide when Tier 1 proves insufficient):** Keepa (€19/mo, Amazon price history); Barcode Lookup (richer fields, subscription); **PriceCharting** (free API, current market values from eBay sold data, good for games/cards/collectibles — add as Tier 2 for those verticals). Stubs not implemented yet.

**Do not implement:** Amazon PAAPI (sunset 2026), GoodReads (discontinued), TCGPlayer (closed), CamelCamelCamel (no API), eBay Finding API (dead 2025).

---

