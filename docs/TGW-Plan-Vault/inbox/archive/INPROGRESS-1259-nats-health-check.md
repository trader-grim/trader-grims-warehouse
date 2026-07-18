# INPROGRESS: todo #1259 nats health check reclassify

Working in worktree `/opt/TGW/var/worktrees/1259-nats-health-check` on branch
`todo/1259-nats-health-check`, off `catio-nix-0.0.1-alpha`.

Task: `nats-py` was already installed into the production venv earlier this
session (Dave-authorized). Verifying `tgw health`'s nats check now reports
module-present-but-no-broker rather than `ModuleNotFoundError`, and if the
messaging/status is misleading, fixing it in `nats_client.py` /
health-check aggregator to classify as warning/info (fire-and-forget,
non-blocking), not a failure. NOT touching #1510 (standing up the broker) —
out of scope.
