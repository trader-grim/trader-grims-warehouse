# TGW Queue Worker Configs

## Files

- `tgw-worker.service` — systemd service unit.
- `queue-worker.yaml` — worker runtime config.

## Install

```bash
sudo install -o root -g root -m 0644 tgw-worker.service /etc/systemd/system/tgw-worker.service
sudo install -o tgw -g tgw -m 0640 queue-worker.yaml /etc/tgw/queue-worker.yaml
sudo systemctl daemon-reload
sudo systemctl enable tgw-worker.service
sudo systemctl start tgw-worker.service
```

## Verify

```bash
systemctl status tgw-worker.service
journalctl -u tgw-worker.service -f
```
