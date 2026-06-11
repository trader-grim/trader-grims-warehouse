# TGW systemd units

## Active units (deploy these)

| File | Purpose | Enabled |
|------|---------|---------|
| `queue-workers.target` | Wants all 17 worker instances; started by timer | static (via timer) |
| `queue-workers-startup.timer` | Fires 10s after boot → starts queue-workers.target | yes |
| `trader-grims-backup.service` | inotify + rsync hardlink snapshot backup | yes |

Units installed separately (not in this directory):

| Unit | Location | Purpose |
|------|----------|---------|
| `tgw-http.service` | `etc/systemd/tgw-http.service` | HTTP API (port 7373) |
| `tgw-worker@.service` | `/etc/systemd/system/` only | Template for all queue workers |
| `tgw-worker@token_refresh.service` | `/etc/systemd/system/` only | Individually enabled as belt+suspenders for token worker |
| `tgw-db-backup.{service,timer}` | `etc/systemd/` | PP-BACKUP-001 A1: daily pg_dump at 03:30 |
| `tgw-cloud-sync.{service,timer}` | `etc/systemd/` | PP-BACKUP-001 A2: daily rclone sync at 02:30 |
| `tgw-secrets-backup.{service,timer}` | `etc/systemd/` | PP-BACKUP-001 A3: monthly secrets bundle at 04:00 on 1st |
| `tgw-offline-sync@.service` | `etc/systemd/` | PP-BACKUP-001 A7: mount-triggered offline drive sync |

## PP-BACKUP-001 Phase A install procedure

**Prerequisites (operator, once):**
1. A2 history-point gate: `rclone copy dbukove:TGW dbukove:TGW-historypoint-20260514` — confirm with `rclone size` both sides before enabling `tgw-cloud-sync.timer`.
2. A3 passphrase custody: write the GPG symmetric passphrase on paper; store off-machine (safe/wallet). Without it the bundle cannot be restored.
3. A1 target directory will be created by the script on first run (`/opt/TGW/var/backups/trader_grims_warehouse/db/`).

```bash
# Copy PP-BACKUP-001 units
sudo cp etc/systemd/tgw-db-backup.service \
        etc/systemd/tgw-db-backup.timer \
        etc/systemd/tgw-cloud-sync.service \
        etc/systemd/tgw-cloud-sync.timer \
        etc/systemd/tgw-secrets-backup.service \
        etc/systemd/tgw-secrets-backup.timer \
        etc/systemd/tgw-offline-sync@.service \
        /etc/systemd/system/

sudo systemctl daemon-reload

# Enable timers (do NOT start tgw-cloud-sync.timer until A2 history-point gate is done)
sudo systemctl enable --now tgw-db-backup.timer
sudo systemctl enable --now tgw-secrets-backup.timer
# After A2 gate: sudo systemctl enable --now tgw-cloud-sync.timer

# Test A1 immediately
sudo systemctl start tgw-db-backup.service
journalctl -u tgw-db-backup.service

# Test A3 immediately (passphrase prompt will appear interactively)
sudo systemctl start tgw-secrets-backup.service
```

**A7 offline drive setup (per drive):**
```bash
LABEL=TGW-SENTRY-01
# Format and label (replace /dev/sdX1 with actual device):
mkfs.ext4 -L "$LABEL" /dev/sdX1
mkdir -p "/media/tgw/$LABEL"
echo "LABEL=$LABEL /media/tgw/$LABEL ext4 noauto,user,relatime 0 2" | sudo tee -a /etc/fstab

# Wire the mount-trigger:
ESCAPED=$(systemd-escape --path "/media/tgw/$LABEL")
sudo mkdir -p "/etc/systemd/system/${ESCAPED}.mount.d"
sudo tee "/etc/systemd/system/${ESCAPED}.mount.d/tgw-sync.conf" <<EOF
[Unit]
Wants=tgw-offline-sync@${LABEL}.service
After=tgw-offline-sync@${LABEL}.service
EOF
sudo systemctl daemon-reload
```

## Install procedure

```bash
# Copy active units
sudo cp systemd/queue-workers.target \
        systemd/queue-workers-startup.timer \
        systemd/trader-grims-backup.service \
        /etc/systemd/system/

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable queue-workers-startup.timer
sudo systemctl enable trader-grims-backup.service

# Start
sudo systemctl start queue-workers-startup.timer
sudo systemctl start trader-grims-backup.service
```

## Boot sequence (fully automatic)

1. `postgresql.service` — DB up (enabled by package install)
2. `tgw-http.service` — HTTP API up (enabled)
3. `ollama.service` — AI models available (enabled)
4. `trader-grims-backup.service` — backup watcher up (enabled)
5. `tgw-worker@token_refresh.service` — token worker up (individually enabled)
6. +10s: `queue-workers-startup.timer` fires → `queue-workers.target` → all 17 workers up

## Retired (see history/trader_grims_warehouse/)

| File | Reason retired |
|------|---------------|
| `queue-launcher.service` | Old Popen-based launcher; replaced by systemd worker template (Phase 1) |
| `tgw-worker.service` | References non-existent module path; pre-Phase 1 |
| `tgw-watcher.service` | inotify pool watcher; replaced by bundle_intake/multi_intake workers |
| `tgw-manage-newitem-pool.py` | Pool manager script; replaced by queue system |
