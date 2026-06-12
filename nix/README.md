# TGW on NixOS (PP-NIXOS-001)

NixOS is the committed target OS. This directory + `../flake.nix` package TGW and
declare its full service stack so it can be **built and booted in a NixOS VM**
before any cutover. Nothing here runs on the current MX host (it has no Nix).

| File | Role |
|------|------|
| `../flake.nix` | Package (`buildPythonApplication`), `nixosModules.tgw`, a `vm` host config, dev shell. |
| `tgw.nix` | NixOS module: tgw user, PostgreSQL `state_machine` DB, tgw-http, worker fleet, backup unit. |

## Validate in a VM (Dave)

On a machine with Nix + flakes enabled:

```bash
cd /opt/TGW/src/trader-grims-warehouse

# 1. Does the flake evaluate + the package build?
nix flake check
nix build .#tgw            # → ./result/bin/tgw

# 2. Boot the whole stack in a throwaway VM:
nixos-rebuild build-vm --flake .#vm
./result/bin/run-*-vm      # QEMU; root password is "tgw" (VM only)

# 3. Inside the VM:
systemctl status tgw-http tgw-worker-ai_identify postgresql
sudo -u tgw psql state_machine -c '\dt'   # ledger tables present?
# (tgw health needs restored secrets — see below)
```

## What the module does / does not provision

**Does:** the `tgw` system user (configurable `uid`, default 999), the
PostgreSQL `state_machine` database owned by `tgw` (local peer auth), one
`tgw-worker-<queue>.service` per queue, `tgw-http`, the `/opt/TGW` directory
tree (tmpfiles), and an opt-in backup unit.

**Does not:** populate `/opt/TGW/secrets` (eBay/Discogs/API-key/token JSON),
`/opt/TGW/config/tgw-api-config.json`, or `/opt/TGW/data` — these come from the
MX restore image / backup (see `../docs/TGW-Plan-Vault/reference/PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md`).
Until secrets are restored, `tgw health` will report the eBay/Discogs checks as
failing; the service stack itself still starts.

## Home-dir-independent layout (`/opt/TGW` is the whole entity)

The `tgw` user has **no home directory** (`createHome = false`) — this is deliberate
so the entire configured runtime lives under `/opt/TGW` and a snapshot/restore of
that one tree carries everything with zero `~tgw` dependency. The module sets a
home-dir-free environment on every long-running tgw unit (`commonService.environment`):

| Var | Value | Why |
|-----|-------|-----|
| `HOME` | `/opt/TGW` | tools that probe `$HOME` land inside the tree, not a missing `~tgw` |
| `NVM_DIR` | `/opt/TGW/.nvm` | nvm installs Node under the tree (markmap-cli etc.) |
| `NPM_CONFIG_PREFIX` | `/opt/TGW/.npm` | global npm installs stay under the tree |

The Python venv already lives at `/opt/TGW/.venvironments`. `systemd.tmpfiles.rules`
pre-creates `/opt/TGW/{.nvm,.npm,.venvironments}` owned by the tgw user. When the
operator installs nvm, do it with `NVM_DIR=/opt/TGW/.nvm` set so Node lands in the
imageable tree. Net effect: `image(/opt/TGW) + flake + site-config = the running system`
with no home-directory state to reconstruct.

> A **separate personal flake** (operator desktop preference apps — Firefox, Plasma
> extras, etc.) is intended to compose via `imports` and is kept out of this platform
> flake on purpose: platform deps and personal preferences stay decoupled.

## Knobs (`services.tgw.*`)

`enable`, `package`, `user`/`group`/`uid`, `dataDir` (keep `/opt/TGW`),
`httpHost`/`httpPort`, `workers` (queue list), `enableHttp`, `enableBackup`.

## Open items to confirm during validation

1. **`python3Packages.mcp` availability** in the pinned `nixos-24.11` channel
   (pyproject needs `mcp>=1.0`). If missing/old, switch the flake input to
   `nixos-unstable` or add an overlay. See the note in `../flake.nix`.
2. **uid alignment** — set `services.tgw.uid` to whatever the restored data is
   owned by so a snapshot restore lines up (PP-DEPLOY-001).
3. **PostgreSQL major version / data migration** — the NixOS `postgresql`
   service initializes a fresh cluster; restoring the MX `state_machine` dump
   (`pg_restore`) is a separate step in the restore runbook.
