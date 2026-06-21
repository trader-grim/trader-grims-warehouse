# TGW NixOS Reference

Comprehensive reference for the TGW NixOS platform. Read `nix/CLAUDE-NIX.md` first —
it has the quick-reference decisions and file map. This document covers the procedures
that need more space: bootstrapping, Syncthing topology, and troubleshooting.

---

## Syncthing topology

**Core principle:** every Syncthing folder lands at the **same path on every machine** that
receives it. Capability differences (workers, inference, ItemData) live in the Nix config
and in which folders Syncthing shares to which devices — never in different paths.

### Full folder map

| Folder | Path (identical everywhere) | MX dev | tgw-prod | tgw-test | portable |
|--------|----------------------------|--------|----------|----------|----------|
| `tgw-install-bundle` | `~/tgw-install-bundle/` | — | **send** | recv | recv |
| `plan-vault` | `~/plan-vault/` | **send** | recv | recv | recv |
| `ItemCatalog` | `/opt/TGW/data/ItemCatalog/` | — | **send** | recv | recv |
| `ItemData` | `/opt/TGW/data/ItemData/` | — | **send** | **not shared** | not shared |
| `tgw-usb-bundle` | `/media/db/TGW-SECRETS` | — | send-only (USB) | — | — |

`ItemData` is excluded from portable/satellite machines at the **Syncthing sharing layer** —
the path `/opt/TGW/data/ItemData/` is the same everywhere it exists, but that folder simply
isn't offered to portable devices. The catalog (SQLite satellite subset) is what portables
carry instead, via `ItemCatalog`.

After NixOS production cutover, tgw-prod takes over as the **send** authority for
`tgw-install-bundle` and `ItemData`. MX either becomes a receiver or is retired.

### Flake distribution — nixos-rebuild --target-host

NixOS configs are **not** distributed via Syncthing. Instead, configs are pushed from MX:

```bash
# Push a config update to any NixOS host (run this on MX, from the git repo):
bash scripts/tgw-push-config.sh tgw-test 100.x.y.z    # Tailscale IP
bash scripts/tgw-push-config.sh tgw-prod 100.x.y.z

# What it runs:
nixos-rebuild switch \
  --flake path:/opt/TGW/src/trader-grims-warehouse#tgw-test \
  --target-host db@100.x.y.z \
  --use-remote-sudo
```

The flake is evaluated **locally on MX**. Only the Nix store closure (compiled
derivations) is transferred to the remote host — the remote never needs the flake source.
No `~/tgw-flake/` directory needed on NixOS hosts; no Syncthing folder for the flake.

**Emergency / offline:** `scripts/tgw-nix-sync.sh` copies the flake source to a local
directory for USB offline kits. Rarely needed — prefer `tgw-push-config.sh`.

### Initial provisioning — nixos-anywhere

For machines with SSH access (including bare-metal running another OS), `nixos-anywhere`
provisions NixOS remotely without physical access after the first boot:

```bash
# Full remote provision — wipes disk and installs from flake config:
nix run github:nix-community/nixos-anywhere -- \
  --flake path:.#tgw-prod \
  root@<IP>

# With secrets injected at provision time (Tailscale auth key, etc.):
mkdir -p /tmp/secrets/run/secrets
echo "tskey-auth-..." > /tmp/secrets/run/secrets/tailscale-key
chmod 600 /tmp/secrets/run/secrets/tailscale-key
nix run github:nix-community/nixos-anywhere -- \
  --flake path:.#tgw-prod \
  --extra-files /tmp/secrets \
  root@<IP>
```

nixos-anywhere: SSH → kexec into RAM installer → Disko partitions disk → NixOS
installs → machine reboots into the new system. Requires Disko config in the host's
flake entry (planned for production cutover — see PLAN-nixos-migration.md).

### Device pairing

Device IDs must be exchanged once per machine pair. With SSH and Tailscale established
(see bootstrap sequence), this is one command:

```bash
# On MX — get the device ID of a newly installed host:
ssh db@<hostname> "syncthing cli config system status | jq -r .myID"
# Paste that ID into Syncthing web UI → Add Device
```

To pre-wire a device ID into the flake (eliminates the web UI step on that host):

```nix
# In nix/tgw/platform.nix or a host-specific file:
services.syncthing.settings.devices."<DEVICE-ID>" = {
  name             = "mx-dev";
  autoAcceptFolders = true;   # accepts any folder MX offers without a UI click
};
```

`autoAcceptFolders = true` means once MX shares `tgw-flake` with a device, that device
accepts automatically — no one needs to click anything on the new machine.

---

## Bootstrap sequence: bare machine → fully connected

**The core rule: stop typing manually the instant the machine has an IP address.**

Both `sshd` (server) and the openssh client are declared in `nix/os/base.nix`:
`services.openssh.enable = true` with `PasswordAuthentication = true`, and port 22 opened
in the firewall. The moment NixOS is installed and the machine has a network address,
SSH in from MX — copy-paste commands from your own keyboard, no more reading off a screen
and typing on another machine. Everything else (KDE Connect, Syncthing, Tailscale) follows
from that first SSH connection.

The bootstrap gap is only the physical install + the moment to get the IP. After that:
SSH → Tailscale → Syncthing, and you never need to touch the new machine's keyboard again.

### Phase 0 — Prepare the USB kit (on MX, once per significant flake update)

```bash
# Update the TGW-SECRETS USB partition with the current flake
sudo bash scripts/tgw-nix-bundle-usb.sh

# Manual alternative if the script isn't available:
mount /dev/disk/by-label/TGW-SECRETS /mnt/tgw-secrets
rsync -a --delete nix/ /mnt/tgw-secrets/flake/nix/
cp flake.nix flake.lock /mnt/tgw-secrets/flake/
umount /mnt/tgw-secrets
```

### Phase 1 — Install NixOS on the new machine (physical access required here)

NixOS ISOs live at `~/tgw-install-bundle/iso/` and sync to all machines via Syncthing's
`tgw-install-bundle` folder. Create a boot stick from any machine that has the bundle:

```bash
# On any machine with ~/tgw-install-bundle/iso/:
lsblk                                # confirm the USB device node first
sudo dd if=~/tgw-install-bundle/iso/nixos-minimal-*.iso of=/dev/sdX bs=4M status=progress
```

```bash
# On the NEW machine:
# Boot from the dd'd USB (Ventoy EFI unreliable on some hardware e.g. A1131 — use dd)
# Partition + mount target disk at /mnt

# Mount the TGW-SECRETS USB kit (different device from /mnt)
mount /dev/disk/by-label/TGW-SECRETS /mnt/kit

bash /mnt/kit/flake/nix/tgw-install.sh <hostname>
# Generates hardware config, commits it to the flake copy, installs NixOS

umount /mnt/kit
reboot
```

After `reboot`: **the new machine is running NixOS with SSH open on port 22.**
Get its IP (look at your router, or it announced via mDNS as `<hostname>.local`), then:

### Phase 2 — SSH in immediately (from MX)

```bash
# On MX — as soon as the new machine is up:
ssh db@<hostname>.local        # mDNS, same LAN
# or
ssh db@<ip-address>

# Password: tgw  (change it now)
passwd
sudo passwd root
```

**You are now on the new machine via SSH. Stop touching its keyboard.**

### Phase 3 — Tailscale (from the SSH session)

Tailscale is already declared in `nix/os/base.nix` and running. Activate it:

```bash
# On the new machine, via SSH:
sudo tailscale up
# Opens a browser auth URL — open it on MX, approve the device
# The new machine is now on the Tailnet
```

After this step, you can SSH to the new machine from anywhere via its Tailscale IP, not
just the local network. Physical proximity is no longer needed.

### Phase 4 — Syncthing pairing (from the SSH session)

Syncthing is used for `plan-vault`, `ItemCatalog`, and `ItemData` — not for the flake.
Pair the new device so those folders sync once the machine is in service.

```bash
# On the new machine, via SSH: get the Syncthing device ID
syncthing cli config system status | jq -r .myID
# Copy that ID (it's in your SSH terminal — paste works)

# On MX, in the Syncthing web UI (http://localhost:8384):
#   Add Device → paste the ID → Save
#   Share relevant folders (plan-vault, etc.) with the new device
```

The new machine's Syncthing web UI will show a "New Device" notification. Accept it.

### Phase 5 — Push the full config from MX

Config updates are pushed from MX, not pulled by the host:

```bash
# On MX, from the git repo root:
bash scripts/tgw-push-config.sh tgw-test <tailscale-ip>
# or for local LAN before Tailscale is paired:
bash scripts/tgw-push-config.sh tgw-test <hostname>.local
```

This evaluates the flake on MX and transfers the built config to the remote host.
After the first `tgw-push-config.sh` run, the host is on the full declared config.

### Steady state — no more USB, no more manual typing

```
Edit .nix files on MX → git commit
  → bash scripts/tgw-push-config.sh <hostname> <tailscale-ip>
  (repeat for each host that needs the update)
```

KDE Connect is available for clipboard sharing between the desktop machines once the
desktop is configured. For headless / SSH-only work, clipboard sharing isn't needed —
copy-paste works naturally in the SSH terminal.

---

## User account cheatsheet

Confusion about which user does what is the #1 cause of "permissions broken" errors.

| Who | Username | uid | Password | Purpose |
|-----|---------|-----|---------|---------|
| Dave (you) | `db` | 1000 | `tgw` (change on first login) | Login, sudo, Syncthing, git |
| TGW service | `tgw` | 900 | no shell / no login | Runs workers, tgw-http, owns /opt/TGW |
| Emergency | `root` | 0 | `tgw` (change on first login) | Only if db is broken |

**Password change on first login:**
```bash
# As db:
passwd         # changes db's password
sudo passwd root   # changes root's password
```

**Why uid 900 for tgw?** System service accounts belong below uid 1000. The MX live user
is currently uid 1001; step 0.6 in PLAN-nixos-migration.md migrates it to 900 before
production cutover so restore never needs a chown pass.

**Syncthing runs as the operator user** (`services.syncthing.user` in `nix/os/base.nix`) — `~/tgw-flake` is owned by that user. Folder paths in the Nix config are derived from this declaration, not hardcoded.
The `tgw-rebuild` alias runs `sudo nixos-rebuild` (db → root), which is fine since
`wheelNeedsPassword = false`.

---

## Common operations

### Push a config update to a remote host (run on MX)
```bash
bash scripts/tgw-push-config.sh tgw-test 100.x.y.z
# expands to: nixos-rebuild switch --flake path:.#tgw-test
#             --target-host db@100.x.y.z --use-remote-sudo
```

### Check the flake without applying (run on MX)
```bash
nix flake check path:/opt/TGW/src/trader-grims-warehouse
```

### Roll back after a bad switch
```bash
sudo nixos-rebuild switch --rollback
# Instant, data-safe. Nix keeps the previous generation.
```

### Boot into the previous generation (for bad reboots)
At the systemd-boot menu: select the previous entry (shown with the old generation number).

### See all installed generations
```bash
sudo nix-env --list-generations --profile /nix/var/nix/profiles/system
```

### Apply a change from MX development (without waiting for Syncthing)
```bash
# On tgw-test, after pulling latest from MX via scp/rsync/USB:
nix flake check path:/tmp/tgw-flake   # validate first
sudo nixos-rebuild switch --flake path:/tmp/tgw-flake#tgw-test
```

### Check Syncthing status
```bash
# Web UI:
http://localhost:8384    # or http://<tailscale-ip>:8384

# CLI:
syncthing cli config system status
```

### Find the Syncthing device ID for pairing
```bash
syncthing cli config system status | jq -r .myID
```

---

## Troubleshooting

### "nix flake check passes but rebuild doesn't see my changes"

You hit the path: trap. Nix evaluated the git-committed state of `~/tgw-flake`,
not the files Syncthing wrote. Check that `tgw-rebuild` uses `path:` prefix:

```bash
# This alias is defined in nix/tgw/platform.nix — verify it says path:
cat ~/.bashrc | grep tgw-rebuild    # or wherever your shell sources it
# Should show: sudo nixos-rebuild switch --flake path:~/tgw-flake#...
```

### "The tgw user lost its groups / permissions broke"

The `tgw` user is declared in `nix/tgw/users.nix`. After `nixos-rebuild switch`, NixOS
re-applies the declared user config. Any groups NOT in the Nix declaration are removed.

Check `nix/hosts/tgw-prod.nix` — it adds `keyd` group:
```nix
users.users.tgw.extraGroups = [ "keyd" ];
```

If tgw needs a new group, add it there (host-specific) or in `tgw/users.nix` (all hosts).

`users.mutableUsers = true` (NixOS default) — passwords changed with `passwd` survive
a rebuild. Group memberships do NOT survive unless declared in Nix.

### "db user lost its password after nixos-rebuild"

`initialPassword` in `nix/os/users.nix` sets the password only if no password hash exists
yet (first boot). It does NOT reset an existing password on rebuild. If the password was
lost, it means it was never changed from the default `tgw`. Set it now: `passwd`.

### "Syncthing is running but the tgw-flake folder isn't appearing"

Syncthing folders must be accepted on both sides. On the receiving host, open
`http://localhost:8384` — there should be a "New Folder" notification to accept.
If the sender never offered the folder, check that the device was added AND that
the folder's devices list includes the new device (Syncthing UI → folder → Edit).

### "nixos-install failed: hardware config already exists"

The install script commits `nix/hardware/<hostname>-hardware.nix`. If you're reinstalling
the same hostname, the file already exists. Either delete it from the repo before running
the installer, or generate manually:
```bash
nixos-generate-config --root /mnt --show-hardware-config > /tmp/hw.nix
# Review, diff against existing, update if different
```

### "mbpfan not available" (A1131 specific)

`services.mbpfan.enable = true` in `tgw-test.nix` requires the `mbpfan` service to be
in nixpkgs. Verify it's present in the pinned channel:
```bash
nix eval nixpkgs#mbpfan.meta.available
```
If absent on the current channel, comment out the mbpfan line temporarily.

### "Module option X doesn't exist" after a channel bump

nixpkgs renames/removes options between versions. When bumping channels:
1. Run `nix flake check` — it will list the unknown options
2. Use Context7 MCP to look up the new option name: `mcp__plugin_context7_context7__query-docs("nixos <option-area>")`
3. Fix before applying to tgw-test

---

## Phase status (keep current)

| Phase | Description | Status |
|-------|-------------|--------|
| 0.1 | Pillow dep unification in pyproject.toml | Partial — in base deps but duplicate in extras |
| 0.2 | Nix module fixes (pg17, schema-init, backup unit) | Partial — uid 900 ✅, check tgw.nix |
| 0.3 | Template unit form in tgw.nix | Verify |
| 0.4 | Config normalization (ISS-003/004) | Unknown |
| 0.5 | Site-config GitHub repo | ✅ Done 2026-06-19 |
| 0.6 | uid migration (MX live tgw user → 900) | Not done — requires downtime |
| 1 | MX rollback ISO bake | Not done |
| 2 | VM validation | Not done |
| 2.5 | USB boot media | ✅ Both drives prepared; A1131 validated 2026-06-20 |
| 3.1 | Install NixOS on A1131 | ✅ Done 2026-06-20 (nixos-25.05 via 26.05 ISO) |
| 3.2 | Syncthing pair + vault sync | In progress |
| 4 | Dress rehearsal (shadow server) | Blocked on Phase 0.6 + 1 |
| 5 | Production cutover | Blocked on Phase 4 |

---

## Hardware notes

### iMac12,1 (A1131 — tgw-test)
- 2011 iMac, Intel Core i5-2400S, 8–16 GB RAM
- **CPU-only** — cannot run Ollama models; inference tests deferred to production hardware
- EFI is 32-bit Apple EFI — Ventoy EFI chainload unreliable; use `dd` to burn NixOS ISO directly
- Btrfs installed with subvolumes: `/` (root), `/home`, `/nix`
- `applesmc` kernel module loaded; `mbpfan` controls fan speed
- `systemd-boot` works fine once EFI entry is written by the installer

### Production host (tgw-prod — current MX machine)
- Full GPU/inference capable
- Hardware config placeholder at `nix/hardware/tgw-prod-hardware.nix` — regenerate with `nixos-generate-config` at install time
- `keyd` macroboard; NFS export for phone photo drop; Ollama

---

## See also

- `nix/CLAUDE-NIX.md` — session guide (decisions, file map, workflow)
- `PLAN-nixos-migration.md` — phase-by-phase plan with risk table and rollback tiers
- `nix/README.md` — brief orientation
- `docs/TGW-Plan-Vault/reference/TGW-Architecture-Services.md` — how services connect
