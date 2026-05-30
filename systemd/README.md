# TGW systemd units

## Deploy these

| File | Action |
|------|--------|
| `queue-launcher.service` | Replace existing — path updated to console script |
| `queue-workers-startup.timer` | Unchanged — redeploy as-is |
| `queue-workers.target` | Unchanged — redeploy as-is |
| `trader-grims-backup.service` | Unchanged — redeploy as-is |

## Retire these (do not redeploy)

| File | Reason |
|------|--------|
| `tgw-worker.service` | Dead — references non-existent module path. Launcher uses Popen, not systemd units. |
| `tgw-watcher.service` | Deprecated — replaced by the queue system. |

## Install procedure

```bash
# Copy units
sudo cp systemd/*.service systemd/*.timer systemd/*.target /etc/systemd/system/

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable queue-workers-startup.timer
sudo systemctl enable trader-grims-backup.service

# Start
sudo systemctl start queue-workers-startup.timer
sudo systemctl start trader-grims-backup.service

# Verify
sudo systemctl status queue-launcher.service
sudo systemctl status trader-grims-backup.service
```

## Notes

- The launcher manages worker processes directly via Popen — it does not
  create systemd units at runtime.
- Workers are configured via `.queue_worker` and `.queue_worker_config`
  symlinks in each queue directory under `/opt/TGW/runtime/state/queues/`.
- The eBay token refresh currently runs as a cron job
  (`ebay_api_token_refresh.py`). Do not remove that cron until the token
  refresh is moved into the queue system.
