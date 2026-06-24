# TGW NixOS — Claude Session Guide

Read this before any Nix work in this repo. It describes the specific decisions, file
boundaries, user model, and workflow for the TGW flake — not generic NixOS.

---

## Architecture: two-layer model

Every host imports from two independent layers:

```
nix/os/        — CatioNIX: OS platform, knows nothing about TGW
nix/tgw/       — TGW application layer, always on top of CatioNIX
```

The test: could this config belong on a host running a *different* application?
- Yes → it belongs in `nix/os/`
- No → it belongs in `nix/tgw/`

Never put TGW-specific things in `nix/os/` and never put platform-generic things
in `nix/tgw/`. Violations break the CatioNIX/TGW boundary.

---

## File map

| File | Owns |
|------|------|
| `nix/os/base.nix` | Timezone, SSH, platform tools (git/rsync/rclone/age/fzf/…), zsh, tailscale, avahi, smartd, Syncthing daemon (runs as `db`), experimental-features |
| `nix/os/users.nix` | Operator account `db` (uid 1000, wheel, networkmanager); root initial password |
| `nix/os/desktop.nix` | X11, SDDM, Qtile, desktop apps — CatioNIX desktop layer |
| `nix/tgw/users.nix` | Service account `tgw` (uid/gid 900) — the ONLY file that may declare it |
| `nix/tgw/platform.nix` | TGW system packages (ffmpeg, imagemagick, exiftool, chafa, gh); Syncthing folder `tgw-install-bundle`; ydotool |
| `nix/tgw/desktop.nix` | Qtile config + TGW status widgets; ydotool |
| `nix/tgw/usb-sync.nix` | USB auto-mount + send-only Syncthing `tgw-usb-bundle` (production only) |
| `nix/tgw.nix` | NixOS module: `services.tgw.*` options — workers, http, PostgreSQL, schema init |
| `nix/bases/master.nix` | Full server base (os/base + os/users + tgw/users + tgw/platform + usb-sync + inference + keyd + nfs). Guard assertions: uid 900 required. |
| `nix/bases/portable.nix` | Client/satellite base (os/base + os/users + tgw/users + tgw/platform). No workers, no HTTP, no PostgreSQL. |
| `nix/inference.nix` | Ollama — production only, server tier |
| `nix/keyd.nix` | keyd macroboard — production only |
| `nix/nfs-exports.nix` | NFS phone photo drop — production only |
| `nix/hosts/tgw-prod.nix` | Production host config |
| `nix/hosts/tgw-test.nix` | iMac12,1 (A1131) test host config |
| `nix/hosts/vm.nix` | Throwaway VM for full-stack validation |
| `nix/hardware/tgw-test-hardware.nix` | A1131 hardware (nixos-generate-config output, committed) |
| `nix/hardware/tgw-prod-hardware.nix` | Production hardware (placeholder; re-generate at cutover) |

---

## Host inventory

| Hostname | Hardware | Role | nixpkgs | Status |
|----------|---------|------|---------|--------|
| `tgw-test` | iMac12,1 (A1131, 2011, Intel Core i5, CPU-only) | NixOS familiarity + flake validation; portable/client tier | 25.05 | Installed 2026-06-20 |
| `tgw-prod` | Production machine | Full server: workers, PostgreSQL, Ollama, keyd, NFS | 25.05 | COMPLETE ✅ 2026-06-22 (session 41) |
| `vm` | QEMU throwaway | Full-stack VM validation before cutover | 25.05 | On-demand |

`tgw-test` **cannot run Ollama models** — hardware limitation. Inference validation
happens at cutover on production hardware (or on upgraded hardware if that arrives first).

---

## User accounts (locked decisions)

| User | uid | Purpose | Who it is |
|------|-----|---------|-----------|
| `db` | 1000 | Operator — Dave's login, runs Syncthing, can sudo | Human |
| `tgw` | 900 | Service account — runs all workers and tgw-http | Systemd services |
| `root` | 0 | Emergency only; initial password `tgw`, change on first login | — |

**Why these names matter:**
- Syncthing runs as the operator user `db` — folder paths derive from `config.services.syncthing.user` so changing the operator name propagates automatically
- Configs are pushed FROM MX via `scripts/tgw-push-config.sh` — NixOS hosts do not store the flake source
- Workers, queue jobs, file ownership — all `tgw:tgw` (uid/gid 900)
- Do NOT confuse `tgw` (service account) with `db` (operator)

`security.sudo.wheelNeedsPassword = false` — db can sudo without a password.

---

## Python app deployment (Option B — current)

The NixOS module manages OS config, systemd units, PostgreSQL, and the `/opt/TGW` tree.
The Python app is **not** built as a Nix package for the server migration. Instead:

- `services.tgw.venvPath` (default: `/opt/TGW/.venvironments/tgw`) points the ExecStart
  binaries at a pip-installed venv — same as the MX setup
- After NixOS install, restore the venv: `pip install -e /opt/TGW/src/trader-grims-warehouse`
- The git repo lives at `/opt/TGW/src/trader-grims-warehouse/` (same as MX)
- `flake.nix` still exposes a `packages.tgw` output (the `buildPythonApplication` build)
  but it is **not wired into the NixOS host configs** until Option A

**Option A (future — after production cutover, applied to tgw-test first):**
`services.tgw.package` fetched from GitHub replaces `venvPath`. This is the hardened
install/upgrade path: `nixos-rebuild switch` updates OS + Python app atomically.

## Locked decisions (do not relitigate)

- **`tgw` uid/gid = 900** — verified free; module guards assert it; MX live user migrates to 900 before cutover (step 0.6 in PLAN-nixos-migration.md)
- **Template unit form** — workers are `tgw-worker@<queue>.service`, NOT `tgw-worker-<queue>`. The tooling (`tgw restart-workers`, `tgwlogs`, runbooks) depends on this.
- **`path:` prefix on Syncthing-received flakes** — see below
- **`system.stateVersion` = freeze at install time** — never update after first boot
- **Secrets never in the Nix store** — no `builtins.readFile` on secrets; secrets restored out-of-band to `/opt/TGW/secrets/` (chmod 700, files 600)
- **Backup unit not in `tgw.nix`** — PP-BACKUP-001 owns it separately
- **PostgreSQL version = 17** — pin `services.postgresql.package = pkgs.postgresql_17` in tgw.nix

---

## The `path:` trap (Syncthing + Nix)

Nix evaluates a flake path as a **git repo** by default. If the flake at `~/tgw-flake`
was synced by Syncthing (not git-committed locally), Nix ignores any changes that aren't
in a git commit.

**Fix:** always use `path:` prefix to force raw filesystem evaluation:

```bash
# WRONG — silently ignores Syncthing-pushed changes if not git-committed:
sudo nixos-rebuild switch --flake ~/tgw-flake#tgw-test

# CORRECT:
sudo nixos-rebuild switch --flake path:~/tgw-flake#tgw-test
```

The `tgw-rebuild` alias in `platform.nix` uses `path:` — do not remove it.

---

## Eval-and-fix workflow (the nix MCP specialist loop)

Before proposing any module change as "done":

1. Draft the change
2. Dave runs: `nix flake check 2>&1 | tee /tmp/nix-check.txt`
3. Paste errors into the conversation (or `! nix flake check` in the Claude Code terminal)
4. Claude reads errors, queries Context7 MCP for the correct nixpkgs option API, proposes correction
5. Repeat until `nix flake check` exits 0

**Never claim a Nix change is complete without a clean `nix flake check`.**

Use Context7 MCP before guessing at option names:
```
mcp__plugin_context7_context7__query-docs("nixos services.syncthing options")
```
NixOS option names are not stable across channels — always verify against the actual channel.

---

## Distribution workflow (steady state, after bootstrap)

```
Edit .nix files on MX (in the git repo)
  → git commit
  → bash scripts/tgw-push-config.sh <hostname> <tailscale-ip>
      # expands to: nixos-rebuild switch --flake path:.#<hostname>
      #             --target-host db@<ip> --use-remote-sudo
```

The flake is **evaluated locally on MX** — the Nix store closure is computed here and
transferred to the remote host.  NixOS hosts do not receive or store the flake source;
only the built derivations land on them.  No Syncthing folder needed for the flake.

**Emergency / offline:** `scripts/tgw-nix-sync.sh` copies the flake source to
`~/tgw-flake/` (or any path via `TGW_NIX_FLAKE_DIR`) for USB kits or offline rebuilds.
This is rarely needed — prefer `tgw-push-config.sh` over Tailscale.

## nixos-anywhere — initial provisioning

For machines that already have SSH access (running any Linux), `nixos-anywhere` can
replace the entire OS remotely without physical access after the first boot:

```bash
# One-command full provision from MX (machine must have SSH + enough RAM for kexec):
nix run github:nix-community/nixos-anywhere -- \
  --flake path:.#tgw-test \
  root@<TARGET_IP>

# Inject secrets at provision time (Tailscale auth key, etc.):
mkdir -p /tmp/secrets/run/secrets
echo "tskey-auth-..." > /tmp/secrets/run/secrets/tailscale-key
chmod 600 /tmp/secrets/run/secrets/tailscale-key
nix run github:nix-community/nixos-anywhere -- \
  --flake path:.#tgw-test \
  --extra-files /tmp/secrets \
  root@<TARGET_IP>
```

**What nixos-anywhere does:** SSH into the target → kexec into a RAM-based NixOS
installer (no USB needed) → Disko partitions the disk → NixOS installs from the
flake config → machine reboots into the new system.

**Requires:** Disko partition config in the host's flake config (see Disko section in
PLAN-nixos-migration.md).  The A1131 was installed manually; nixos-anywhere will be
used for the production cutover (MX → tgw-prod).

**Tailscale automation:** `services.tailscale.authKeyFile = "/run/secrets/tailscale-key"`
in the NixOS config + `--extra-files` at provision time → machine joins the Tailnet on
first boot, no interactive auth needed.

See `docs/TGW-Plan-Vault/reference/TGW-NixOS-Reference.md` for the full folder map across
all machine types (MX, tgw-prod, tgw-test, portable).

---

## Common operations

**Add a system package to all TGW hosts:**
Edit `nix/tgw/platform.nix` → `environment.systemPackages`.

**Add a package only to production (server):**
Edit `nix/inference.nix` or `nix/hosts/tgw-prod.nix` directly.

**Add a new NixOS host:**
1. Boot NixOS ISO, partition + mount at `/mnt`
2. Copy flake repo to `/tmp/tgw-flake` (USB or git clone)
3. `bash /tmp/tgw-flake/nix/tgw-install.sh <hostname>` — generates hardware.nix, commits, installs
4. First boot: pair Syncthing via web UI at `http://localhost:8384` (or tailscale IP)
5. Share `tgw-flake` folder with production machine
6. After first sync: `tgw-rebuild` takes over all future updates

**Apply a config change to tgw-test right now:**
```bash
# On tgw-test (as db):
tgw-rebuild    # reads path:~/tgw-flake#tgw-test
```

**Roll back a bad switch:**
```bash
sudo nixos-rebuild switch --rollback
```
Generation rollback is instant, data-safe.

---

## Channel upgrade checklist

When bumping nixpkgs (e.g. 25.05 → 26.05):
1. Update `flake.nix` input: `nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";`
2. `nix flake update` — refreshes `flake.lock`
3. `nix flake check` on MX — fix any module API changes
4. Test on `tgw-test` first: `tgw-rebuild` and verify services
5. Production after tgw-test is stable

Do NOT update `system.stateVersion` when changing channels. stateVersion is frozen at
the version NixOS was first installed on that disk.

---

## Related files

- `PLAN-nixos-migration.md` — phase-by-phase production cutover plan
- `docs/TGW-Plan-Vault/reference/TGW-NixOS-Reference.md` — bootstrap sequence, Syncthing topology, troubleshooting
- `nix/README.md` — brief orientation
