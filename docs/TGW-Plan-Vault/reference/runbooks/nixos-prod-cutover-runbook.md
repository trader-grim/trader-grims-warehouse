# NixOS Production Cutover — Standalone Runbook

**Written:** 2026-06-23. Execute this without Claude Code assistance.
**Context:** MX DR abandoned. dd image of nvme0n1p2 taken. Proceeding to NixOS install.
**Flake target:** `tgw-prod` (bases/master.nix + desktop + dev layers)

## Preferred path: install FROM the A1131 (nixos-anywhere)

The A1131 (tgw-test) has NixOS + full Nix toolchain + internet. Use it as the operator
machine — nixos-anywhere SSHes into the production machine (ISO booted) and handles
disko + install in one shot. Much cleaner than the sda5 chroot fallback below.

**On the production machine (ISO boot):**
```bash
service ssh start        # start sshd (MX ISO) — or: systemctl start ssh
passwd root              # set password so A1131 can authenticate
ip addr show | grep "inet "   # note the IP
```

**On the A1131:**
```bash
# Get the flake (clone from GitHub, or from TGW-VAULT USB):
git clone https://github.com/<org>/trader-grims-warehouse /tmp/tgw-flake
# Verify disko fixes present:
grep device /tmp/tgw-flake/nix/hosts/tgw-prod-disko.nix   # must say nvme0n1
grep "200G" /tmp/tgw-flake/nix/hosts/tgw-prod-disko.nix   # must say 200G (not 500G)

# Run — generates hardware config automatically, handles disko + install:
nix run github:nix-community/nixos-anywhere -- \
  --generate-hardware-config nixos-generate-config /tmp/tgw-prod-hardware.nix \
  --flake path:/tmp/tgw-flake#tgw-prod \
  root@<PROD-IP>
```

After production machine boots successfully, commit the generated hardware config from A1131:
```bash
cp /tmp/tgw-prod-hardware.nix /tmp/tgw-flake/nix/hardware/tgw-prod-hardware.nix
cd /tmp/tgw-flake
git add nix/hardware/tgw-prod-hardware.nix nix/hosts/tgw-prod-disko.nix
git commit -m "feat: tgw-prod hardware config and disko fixes"
git push
```

Then skip to **Phase H** (first boot checklist) below.

---

## Fallback path: install from sda5 chroot (no A1131 available)

Use this if the A1131 is unreachable or SSH cannot be established on the ISO boot.

---

---

## Data safety checklist (verify before touching nvme0n1)

- [ ] pg_dump exists: `data/dumps/db-backup-PRE-NIXOS-20260622T164601.dump` on nvme0n1p3
- [ ] ItemData on sde1 (label: TGW-ITEMDATA) — separate disk, will NOT be touched
- [ ] TGW-VAULT USB available (has flake copy, site-config, secrets)
- [ ] dd image of nvme0n1p2 taken (confirmed by Dave 2026-06-23)

---

## Phase A — Prep (before touching nvme0n1)

### A1. Confirm disk layout

```bash
lsblk -o NAME,SIZE,LABEL,FSTYPE,MOUNTPOINT
```

Must see:
- `nvme0n1` (~477G) — target for wipe
- `sda5` (label: `tgw-catio-nix`, ~80G btrfs) — Nix environment source
- `sde1` (label: `TGW-ITEMDATA`) — DO NOT TOUCH

### A2. Save uncommitted plan changes to ItemData disk

```bash
mkdir -p /mnt/itemdata
mount /dev/disk/by-label/TGW-ITEMDATA /mnt/itemdata
mkdir -p /mnt/itemdata/_pre-nixos-backup

cp /media/db/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md \
   /mnt/itemdata/_pre-nixos-backup/
cp /media/db/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/plan/PLAN-nixos-migration.md \
   /mnt/itemdata/_pre-nixos-backup/

# Also copy the pg_dump to sde1 as a second copy
cp /media/db/TGW/data/dumps/db-backup-PRE-NIXOS-20260622T164601.dump \
   /mnt/itemdata/_pre-nixos-backup/
```

### A3. Mount TGW-VAULT USB

```bash
mkdir -p /mnt/vault
mount /dev/disk/by-label/TGW-VAULT /mnt/vault
ls /mnt/vault/flake/ /mnt/vault/secrets/ /mnt/vault/dumps/
```

If TGW-VAULT not available, the flake is still on nvme0n1p3 (copy it in A4).

---

## Phase B — Set up Nix environment from sda5

### B1. Mount sda5 subvolumes

```bash
mkdir -p /mnt/nixos
mount -o subvol=@ /dev/sda5 /mnt/nixos

# Try these — if a subvol doesn't exist, skip it
mkdir -p /mnt/nixos/nix
mount -o subvol=@nix /dev/sda5 /mnt/nixos/nix   # may fail if nix is inside @
mkdir -p /mnt/nixos/home
mount -o subvol=@home /dev/sda5 /mnt/nixos/home  # may fail if home is inside @

mount -t proc proc /mnt/nixos/proc
mount --rbind /sys /mnt/nixos/sys
mount --rbind /dev /mnt/nixos/dev
mount --rbind /run /mnt/nixos/run
```

### B2. Enter chroot

```bash
chroot /mnt/nixos /run/current-system/sw/bin/bash --login
# or if that fails:
chroot /mnt/nixos /bin/bash --login
```

Verify Nix works:
```bash
nix --version        # must print something
nixos-install --help
```

### B3. Check internet (nixos-install needs it for substitutes)

```bash
ping -c 2 cache.nixos.org
```

If no reply, bring up the network:
```bash
ip link                          # find interface name (e.g. enp3s0, eth0)
ip link set <interface> up
dhclient <interface>             # or: systemctl start NetworkManager
ping -c 2 1.1.1.1
```

If internet is completely unavailable, see **Appendix: Offline Install** below.

---

## Phase C — Copy and verify the flake

### C1. Get a writable copy of the flake

```bash
# Option 1: from TGW-VAULT USB (preferred — already has disko fix)
mkdir -p /mnt/vault
mount /dev/disk/by-label/TGW-VAULT /mnt/vault
cp -r /mnt/vault/flake /tmp/tgw-flake

# Option 2: from nvme0n1p3 (if TGW-VAULT unavailable)
mkdir -p /mnt/tgw
mount /dev/nvme0n1p3 /mnt/tgw
cp -r /mnt/tgw/src/trader-grims-warehouse /tmp/tgw-flake
umount /mnt/tgw   # MUST unmount before disko runs
```

### C2. Verify the disko fix is present

```bash
grep device /tmp/tgw-flake/nix/hosts/tgw-prod-disko.nix
# Must print: device = "/dev/nvme0n1";
grep "size.*G" /tmp/tgw-flake/nix/hosts/tgw-prod-disko.nix | head -5
# LVM size must be 200G (not 500G)
```

If it says `/dev/sda` or `500G`, the old flake copy was used. Edit it:
```bash
sed -i 's|device = "/dev/sda";|device = "/dev/nvme0n1";|' \
    /tmp/tgw-flake/nix/hosts/tgw-prod-disko.nix
sed -i 's|size     = "500G";|size     = "200G";|' \
    /tmp/tgw-flake/nix/hosts/tgw-prod-disko.nix
```

---

## Phase D — Format disk (POINT OF NO RETURN)

nvme0n1p3 (TGW data partition) will be destroyed. Confirm:
- [ ] Plan docs saved to sde1 (A2)
- [ ] pg_dump saved to sde1 or TGW-VAULT (A3)
- [ ] Flake copy in /tmp/tgw-flake (C1)
- [ ] nvme0n1p3 unmounted

```bash
# From inside the sda5 chroot:
nix run github:nix-community/disko -- \
  --mode disko \
  /tmp/tgw-flake/nix/hosts/tgw-prod-disko.nix
```

This creates and mounts:
- `/mnt/boot` (512M EFI vfat)
- `/mnt` (50G ext4 root via LVM)
- `/mnt/home` (20G ext4 via LVM)
- `/mnt/nix` (80G ext4 via LVM)
- `/mnt/var/lib/postgresql` (50G XFS via LVM)
- `/mnt/opt/TGW` (rest of disk, btrfs subvol @tgw)

Verify:
```bash
df -h /mnt /mnt/nix /mnt/home /mnt/opt/TGW /mnt/var/lib/postgresql /mnt/boot
mount | grep /mnt
```

---

## Phase E — Generate hardware config

```bash
nixos-generate-config --root /mnt --no-filesystems
cat /mnt/etc/nixos/hardware-configuration.nix
```

Copy into the flake:
```bash
cp /mnt/etc/nixos/hardware-configuration.nix \
   /tmp/tgw-flake/nix/hardware/tgw-prod-hardware.nix
```

---

## Phase F — Install NixOS

```bash
nixos-install \
  --root /mnt \
  --flake path:/tmp/tgw-flake#tgw-prod \
  --no-root-passwd
```

This takes 5–20 minutes depending on cache hits. Watch for errors.

Common failure: flake eval error → means the hardware-configuration.nix has a conflicting
`fileSystems` entry. Edit it to remove `fileSystems.*` lines (disko manages those):
```bash
nano /tmp/tgw-flake/nix/hardware/tgw-prod-hardware.nix
# Delete any fileSystems.* and swapDevices.* blocks
# Keep: boot.initrd.availableKernelModules, boot.kernelModules, hardware.cpu.*, nixpkgs.*
```

---

## Phase G — Set root password and reboot

```bash
nixos-enter --root /mnt -c 'passwd db'  # set db operator password
# Also set tgw service account password if needed (peer auth = not usually needed)

reboot
```

NixOS should be default in GRUB. If it isn't, select it manually.

---

## Phase H — First boot post-install checklist

Login as `db`. Run in order:

```bash
# 1. Verify basic system health
systemctl status        # check for major failures
ip addr                 # confirm network
sudo tailscale up --authkey <key>

# 2. Clone the git repo (source of truth)
git clone https://github.com/<org>/trader-grims-warehouse /opt/TGW/src/trader-grims-warehouse
# IMPORTANT: commit the hardware config immediately
cd /opt/TGW/src/trader-grims-warehouse
cp /mnt/etc/nixos/hardware-configuration.nix nix/hardware/tgw-prod-hardware.nix
# (or retrieve from /tmp/tgw-flake if still accessible on sda5)
git add nix/hardware/tgw-prod-hardware.nix nix/hosts/tgw-prod-disko.nix
git commit -m "feat: tgw-prod hardware config and disko device fix"
git push

# 3. Restore site-config
mount /dev/disk/by-label/TGW-VAULT /mnt/vault
mkdir -p /opt/TGW/config
rsync -av /mnt/vault/site-config/ /opt/TGW/config/   # adjust paths per site-config layout

# 4. Restore secrets
mkdir -p /opt/TGW/secrets
cp /mnt/vault/secrets/* /opt/TGW/secrets/
chmod 700 /opt/TGW/secrets
chmod 600 /opt/TGW/secrets/*

# 5. Restore pg_dump
sudo -u tgw pg_restore \
  --clean --if-exists \
  -d state_machine \
  /mnt/itemdata/_pre-nixos-backup/db-backup-PRE-NIXOS-20260622T164601.dump
# (or from TGW-VAULT if available)

# 5b. Verify sequences after pg_restore (sequences can be lost on partial restores)
#     Symptom: `tgw todo --add ...` fails with "null value in column id"
sudo -u tgw psql state_machine -c "SELECT last_value FROM todo_items_id_seq;" 2>/dev/null \
  || sudo -u tgw psql state_machine -c "
      CREATE SEQUENCE todo_items_id_seq START $(
        sudo -u tgw psql state_machine -tAc 'SELECT COALESCE(max(id),0)+1 FROM todo_items'
      );
      ALTER TABLE todo_items ALTER COLUMN id SET DEFAULT nextval('todo_items_id_seq');
      ALTER SEQUENCE todo_items_id_seq OWNED BY todo_items.id;"

# 6. Rebuild Python venv
sudo -u tgw python3 -m venv /opt/TGW/.venvironments/tgw --clear
sudo -u tgw /opt/TGW/.venvironments/tgw/bin/pip install \
  -e /opt/TGW/src/trader-grims-warehouse

# 7. Fix whisper_bin in config (NixOS uses 'whisper-cli', not absolute path)
#    Edit /opt/TGW/config/tgw-api-config.json: "whisper_bin": "whisper-cli"

# 8. TGW health check
sudo -u tgw tgw health

# 9. Claude Code setup (see Claude Code reconnect section below)
```

---

## Claude Code reconnect plan

### If something goes wrong mid-install and you need Claude help

Options (in order of ease):
1. **From another device** — open claude.ai in a browser, paste the key context below
2. **Boot sda5 NixOS** — it's intact; `claude` should be installable there: `npm install -g @anthropic-ai/claude-code`
3. **Reboot to April ISO** — mount nvme0n1 new NixOS partition, inspect what happened

**Context paste for crisis recovery (give this to a new Claude session):**
```
TGW NixOS migration in progress. MX Linux DR abandoned 2026-06-23. 
pg_dump: data/dumps/db-backup-PRE-NIXOS-20260622T164601.dump (done)
ItemData: intact on sde1 (TGW-ITEMDATA label)
Flake: nix/hosts/tgw-prod-disko.nix — device=/dev/nvme0n1, LVM=200G
Phase completed so far: [describe where you got stuck]
Repo: trader-grims-warehouse (private GitHub)
Reference: docs/TGW-Plan-Vault/plan/PLAN-nixos-migration.md Phase 5
```

### After a successful install — setting up Claude Code

```bash
# Node.js comes from os/dev.nix — should already be available
node --version && npm --version

# Install Claude Code
npm install -g @anthropic-ai/claude-code

# Set API key (two options):
# Option A: env var (add to ~/.config/fish/config.fish or ~/.bashrc)
export ANTHROPIC_API_KEY="<key from secrets>"

# Option B: claude login
claude login

# Start a session
cd /opt/TGW/src/trader-grims-warehouse
claude
```

On first session after install, Claude will:
1. Read CLAUDE.md (tells it to process inbox and read master plan)
2. Read the master plan (which captures the current state including "Phase 5 active")
3. Pick up where we left off

**No memory continuity** from the old session (memory was at the ISO mount path, not `/opt/TGW`).
The plan files in git are the state record — they're sufficient.

### Restoring Claude Code settings from db-home disk

The `db` user's home on sdf1 (label: `db-home`) may have `~/.claude/settings.json` with:
- Hooks, permission allow-lists, MCP server registrations

```bash
# Mount the old home disk
mount /dev/disk/by-label/db-home /mnt/db-home

# Inspect what's there
ls /mnt/db-home/.claude/

# Selectively restore
mkdir -p ~/.claude
cp /mnt/db-home/.claude/settings.json ~/.claude/   # API key + permissions
# Do NOT blindly copy everything — old paths may be wrong on NixOS
```

The MCP server (`tgw-mcp-server`) needs re-registration after the venv is rebuilt.
Add to `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "tgw": {
      "command": "/opt/TGW/.venvironments/tgw/bin/tgw-mcp-server",
      "args": []
    }
  }
}
```

---

## Appendix: Offline Install

If `ping cache.nixos.org` fails and you cannot bring up network:

**Option A: Use only sda5's cached packages**
```bash
nixos-install \
  --root /mnt \
  --flake path:/tmp/tgw-flake#tgw-prod \
  --no-root-passwd \
  --option substitute false
```
This builds everything from source using only what's in sda5's `/nix/store`.
Will succeed for packages already cached (base NixOS, systemd, PostgreSQL likely cached
since tgw-test uses the same nixpkgs version). Will FAIL for TGW-specific Python packages
not in the store (pip packages, etc. — but those come via the venv anyway, not nixpkgs).

**Option B: Start with a simpler config first**
If tgw-prod fails offline, install `tgw-test` (simpler, more likely cached) first,
get internet working, then `nixos-rebuild switch --flake path:...#tgw-prod`.

```bash
nixos-install \
  --root /mnt \
  --flake path:/tmp/tgw-flake#tgw-test \
  --no-root-passwd \
  --option substitute false
```

---

## Rollback options (if NixOS install fails)

- **T0:** Something failed in Phase D or E — nvme0n1 is wiped. Boot April ISO, inspect. sda5 still intact for another attempt.
- **T1:** nixos-install succeeded but first boot fails — from GRUB, select previous generation (if any) or boot sda5 NixOS for inspection.
- **T2:** Full retreat — dd image of nvme0n1p2 is the MX restore artifact. Restore using the PP-DEPLOY-001 runbook. ItemData on sde1 is untouched throughout.

## Flutter cache: restore execute bits after rsync

**Symptom:** `flutter build` or `flutter doctor` fails with `permission denied` on `dart`, `dartvm`, or `gen_snapshot`.

**Cause:** `rsync` without `--executability` strips execute bits from flutter cache binaries. Happens after any restore or migration rsync of the `flutter/` tree.

**Fix:**
```bash
sudo -u tgw bash /opt/TGW/src/trader-grims-warehouse/bin/flutter-fix-perms
```

**Prevention:** always rsync flutter/ with `-a` (archive, includes `-p` permissions). Syncthing preserves permissions by default.
