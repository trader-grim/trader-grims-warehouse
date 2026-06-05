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
