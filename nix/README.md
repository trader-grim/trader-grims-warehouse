# TGW on NixOS (PP-NIXOS-001)

NixOS is the committed target OS. This directory + `../flake.nix` package TGW and
declare its full service stack so it can be **built and booted in a NixOS VM**
before any cutover. Nothing here runs on the current MX host (it has no Nix).

## Architecture: CatioNIX + TGW

The nix config is split into two clean layers:

| Layer | Directory | Knows about TGW? |
|-------|-----------|-----------------|
| **CatioNIX** | `nix/os/` | No — would work if TGW were removed |
| **TGW application** | `nix/tgw/` | Yes — built on top of CatioNIX |

This boundary means desktop changes cannot touch server config, server changes cannot
touch user accounts, and CatioNIX can eventually become its own standalone OS.

## Directory structure

```
nix/
  os/                   ← CatioNIX OS layer (TGW-agnostic)
    base.nix            — timezone, SSH [22], admin tools, tailscale, avahi, syncthing
                          service (user=db), smartd, ydotool, zsh, zoxide, nix flakes
    users.nix           — db (uid 1000, wheel+networkmanager), root passwords, sudo
    desktop.nix         — X11+SDDM+Qtile (enabled, no config), KDE Connect,
                          bluetooth+blueman, Logitech, printing, desktop apps, allowUnfree

  tgw/                  ← TGW application layer
    users.nix           — tgw service account (uid/gid 900, isSystemUser).
                          ONLY FILE THAT MAY DECLARE THE TGW SERVICE ACCOUNT.
    platform.nix        — syncthing tgw-flake folder (/home/db/tgw-flake),
                          tgw-rebuild shell alias
    desktop.nix         — Qtile extraPackages (httpx+psycopg2 for tgw_widgets.py),
                          /etc/qtile config files, db's ~/.config/qtile tmpfiles symlinks

  bases/
    master.nix          — Full server platform: os/{base,users} + tgw/{users,platform}
                          + inference + keyd + nfs-exports; services.tgw.enable;
                          bootloader defaults; guard assertions on tgw uid/gid.
    portable.nix        — Client/satellite tier: os/{base,users} + tgw/{users,platform},
                          no workers/http/inference. Forward-looking for satellite hosts.

  hosts/
    vm.nix              — Throwaway VM (nixos-rebuild build-vm --flake .#vm).
                          master base, headless, root password = "tgw".
    tgw-test.nix        — iMac12,1 spare; portable base + os/desktop + tgw/desktop.
                          Client-shaped, full desktop, mbpfan for fan control.
    tgw-prod.nix        — Production host; master base + os/desktop + tgw/desktop.
                          Adds tgw user to keyd group for macroboard access.

  hardware/
    tgw-prod-hardware.nix  — Placeholder; replace with nixos-generate-config output.
    tgw-test-hardware.nix  — iMac12,1 (btrfs, EFI, applesmc, kvm-intel, Intel CPU).

  tgw.nix               — NixOS service module: worker fleet, tgw-http, PostgreSQL
                          state_machine, /opt/TGW tmpfiles tree, backup unit.
                          Declares services.tgw.* options.
                          Does NOT declare users — that is tgw/users.nix.
  inference.nix         — Ollama + whisper.cpp (production; skip on iMac12,1)
  keyd.nix              — keyd macroboard remapping (production only)
  nfs-exports.nix       — NFS server enable + firewall [2049] + exports for
                          phone photo-drop queue (production only; self-contained)

  qtile/
    config.py           — Qtile window manager config (TGW-themed)
    tgw_widgets.py      — Custom Qtile widgets: queue health bar, HTTP API status
  keyd-macroboard.conf  — keyd key-remap config for the TGW intake macroboard
  tgw-install.sh        — Single-script NixOS installer for any TGW host
```

## Module ownership contract (the isolation guarantee)

| What | Owner | Rule |
|------|-------|------|
| Human accounts (db, root) | `os/users.nix` | No other file declares human users |
| TGW service account (tgw) | `tgw/users.nix` | No other file declares the tgw user/group |
| GUI surface (X11, apps) | `os/desktop.nix` | CatioNIX layer; no TGW awareness |
| TGW Qtile config + widgets | `tgw/desktop.nix` | TGW layer; layered on top of os/desktop |
| Server platform composition | `bases/master.nix` | Imports both layers + inference + keyd + nfs |
| Worker fleet, DB, tmpfiles | `nix/tgw.nix` module | Referenced by name only in bases |
| NFS server + firewall 2049 | `nfs-exports.nix` | Self-contained; imported only by master |

Guard assertions in `bases/master.nix` make the build fail loudly if `tgw/users.nix`
is accidentally dropped — the service account cannot be silently removed.

## Validate in a VM (Dave)

On a machine with Nix + flakes enabled:

```bash
cd /opt/TGW/src/trader-grims-warehouse

# 1. Does the flake evaluate + the package build?
nix flake check
nix build .#tgw            # → ./result/bin/tgw

# 2. Spot-check module wiring:
nix eval .#nixosConfigurations.tgw-prod.config.users.users.tgw.uid   # → 900
nix eval .#nixosConfigurations.tgw-prod.config.users.users.db.uid    # → 1000
nix eval .#nixosConfigurations.tgw-test.config.services.tgw.workers  # → [ ]

# 3. Boot the whole stack in a throwaway VM:
nixos-rebuild build-vm --flake .#vm
./result/bin/run-*-vm      # QEMU; root password is "tgw" (VM only)

# 4. Inside the VM:
systemctl status tgw-http tgw-worker-ai_identify postgresql
sudo -u tgw psql state_machine -c '\dt'
```

## Isolation smoke test

Temporarily remove `../tgw/users.nix` from `bases/master.nix`, run `nix flake check`.
Must **fail** with:

> tgw user must exist at uid 900 (nix/tgw/users.nix)

Re-add the import when done.

## What the module does / does not provision

**Does:** tgw system user (uid/gid 900, via tgw/users.nix), PostgreSQL `state_machine`
database, one `tgw-worker-<queue>.service` per enabled queue, `tgw-http`, the `/opt/TGW`
directory tree (tmpfiles), and an opt-in backup unit.

**Does not:** populate `/opt/TGW/secrets`, `/opt/TGW/config/tgw-api-config.json`, or
`/opt/TGW/data` — restore from backup before `tgw health` will pass eBay/Discogs checks.

## Knobs (`services.tgw.*`)

`enable`, `package`, `user`/`group`/`uid` (default 900), `dataDir` (keep `/opt/TGW`),
`httpHost`/`httpPort`, `workers` (queue list), `enableHttp`, `enableBackup`.

## Open items to confirm during validation

1. **`python3Packages.mcp`** availability in nixos-25.05 (pyproject needs `mcp>=1.0`).
2. **uid alignment** — uid 900 must match `/opt/TGW` file ownership after the
   live MX `usermod -u 900` migration (PLAN-nixos-migration.md step 0.6).
3. **PostgreSQL major version** — NixOS initialises a fresh cluster; restoring the MX
   `state_machine` dump (`pg_restore`) is a separate restore-runbook step.
