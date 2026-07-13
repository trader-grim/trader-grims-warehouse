# Tigwa report — tgw-prod Hermes-lite MCP and shadow monitoring setup

**From:** Tigwa  
**To:** Claude / TGW plan intake  
**Date:** 2026-07-12  
**Related:** PP-HERMES-EA-001, todo #1344, PP-KNOWLEDGE-001, PP-EVENTD-001  
**Status:** Implementation live; canonical reconciliation and any tracker disposition belong to Claude/operator gate

## Governance acknowledgment

Tigwa previously edited `CLAUDE.md` directly and created todo #1344 while acting on Dave's request. That crossed the intended reporting boundary. Tigwa should have submitted an inbox note for Claude to reconcile rather than writing canonical startup/governance material directly. Dave's authority was not the problem; Tigwa chose the wrong route.

Going forward, Tigwa will use `docs/TGW-Plan-Vault/inbox/` for observations, reports, proposals, and session breadcrumbs and will not directly edit `CLAUDE.md`, `TGW-Master-Plan.md`, PP files, or `TGW-Taskboard.md`. Structured work should use a Tigwa agent tag once that value exists and is approved. Until then, Tigwa will not invent or silently reuse an incorrect agent identity.

Claude may revise, absorb, archive, or reject this report through the normal plan-intake process. Nothing in this report is a request to bypass the operator gate.

## Dave's clarified direction

Monitoring belongs on **tgw-prod** as Tigwa-lite. a1131 remains the full Tigwa office/heavy-work host. The intended current authority is read-only detect-and-flag, matching PP-HERMES-EA-001 shadow mode. Inbox processing, todo writes, enqueue/add-suggest, worker changes, and remediation remain supervised.

## Existing service state verified

The unmanaged systemd user service on tgw-prod was already installed by Claude:

- Unit: `/home/db/.config/systemd/user/hermes-gateway.service`
- Active and running
- Enabled
- `Restart=always`
- User `db` has lingering enabled
- No Nix flake changes were made by Tigwa
- This preserves the settled #1321 userspace-only Hermes decision

## MCP verification found a live defect

`hermes mcp list` reported server `tgw` enabled, but live testing failed:

```text
hermes mcp test tgw
Connection failed: Connection closed
```

Root cause reproduced directly:

- Gateway working directory was `/home/db/.hermes`.
- MCP command switched to user `tgw`.
- FastMCP/Pydantic attempted to inspect relative `.env` in the current directory.
- User `tgw` could not stat `/home/db/.hermes/.env`.
- MCP exited with `PermissionError: [Errno 13] Permission denied: '.env'`.

Hermes' native MCP configuration does not support a `cwd` key. Sudo's `-D` working-directory option was tested but denied by the existing sudo policy for this command.

## MCP correction applied

Created userspace launcher outside the TGW repository:

```text
/home/db/.hermes/scripts/tgw-mcp-readonly.sh
```

Behavior:

1. `cd /opt/TGW/src/trader-grims-warehouse`
2. Execute the existing read-only MCP command as user `tgw`:

```text
sudo -u tgw env TGW_MCP_READONLY=1 /opt/TGW/.venvironments/tgw/bin/python -m tgw.mcp_server
```

Updated `/home/db/.hermes/config.yaml` MCP entry to use that launcher. Added:

- `timeout: 120`
- `connect_timeout: 30`
- MCP sampling disabled

A backup was retained:

```text
/home/db/.hermes/config.yaml.pre-tigwa-lite-20260712
```

Restarted only the existing Hermes user gateway service after config validation.

### MCP live acceptance

`hermes mcp test tgw` now:

- Connects successfully in approximately 1.0–1.1 seconds
- Discovers exactly 8 tools:
  1. `tgw_get_item`
  2. `tgw_search_items`
  3. `tgw_queue_status`
  4. `tgw_health`
  5. `tgw_get_todo`
  6. `tgw_dead_letter`
  7. `tgw_hint_trail`
  8. `tgw_catalog_verify`
- Does not expose enqueue or add-suggest
- Uses `TGW_MCP_READONLY=1`
- Has sampling disabled

## Tigwa-lite shadow monitor installed

Created:

```text
/home/db/.hermes/scripts/tigwa_lite_monitor.py
```

Properties:

- Script-only; no LLM calls
- Read-only
- Performs no TGW remediation or mutation
- Records every snapshot
- Emits stdout only for baseline or changed anomalies
- Avoids repeating unchanged chronic warnings

It observes:

- `/opt/TGW/var/run/thermal.status`
- `tgw health` structured JSON
- Queue depths and dead-letter totals from the health result
- Active `tgw-worker@*` services
- Host boot ID and post-reboot worker-set recheck
- `tgw plan check`
- `tgw plan status` and blocked-count increases

It flags:

- Thermal state transitions and HOT/THROTTLE/SHUTDOWN
- Changed TGW failed-check sets
- Queue spikes
- Dead-letter increases
- Worker additions/removals, including resurrection drift
- Host reboot
- Non-clear plan reconciliation
- Increased blocked-plan total
- Command or JSON parsing failures

It does not:

- Restart or stop workers
- Process inbox files
- Modify plans or taskboard
- Create/update todos
- Enqueue jobs or suggestions
- Attempt to repair detected conditions

## Monitor state and audit

```text
/home/db/.hermes/tigwa-lite/latest.json
/home/db/.hermes/tigwa-lite/state.json
/home/db/.hermes/tigwa-lite/history.jsonl
/home/db/.hermes/tigwa-lite/alerts.jsonl
```

Permissions:

- Directory: 700
- Files: 600

Operating contract:

```text
/home/db/.hermes/TIGWA-LITE.md
```

## Cron job

Created Hermes script-only cron job:

- ID: `d82b5a2ba6e3`
- Name: `tigwa-lite-shadow-monitor`
- Schedule: every 5 minutes, forever
- Mode: no-agent/script-only
- Delivery: local
- Script: `tigwa_lite_monitor.py`

An initial `5m` schedule was caught as one-shot during verification, removed, and replaced with the recurring `every 5m` form.

### Scheduler live acceptance

- Forced run completed successfully.
- Tigwa then waited through an automatic scheduler tick.
- `history.jsonl` advanced from 5 to 6 snapshots without manual triggering.
- Automatic run recorded at 2026-07-12 18:31 local.
- Next run scheduled at 18:36.
- Gateway remained active with zero restarts.

## Monitor bug found and corrected during verification

`tgw health` exits nonzero when subsystem health is false even while returning valid structured JSON. The first script version treated that as command execution failure.

Corrected behavior:

- Valid JSON containing a `checks` list means the health command executed correctly.
- `health.ok=false` and failed checks are monitored as platform state, not mistaken for process failure.
- Two consecutive post-fix runs produced no output when state was unchanged.

## Initial observed baseline

At establishment:

- Thermal: `NORMAL`, approximately 70°C
- Health failed set:
  - `backups`
  - `ebay_sync_fallback`
  - `nats`
- Dead letters: 2,973
- Active workers: 14
- `tgw plan check`: all clear
- Blocked-plan total: 2

These are observations only. Tigwa is not declaring the failed checks acceptable or resolved.

## Known open gap: no alert delivery channel

The tgw-prod gateway logs report no messaging platforms enabled. Monitoring and local alert retention are live, but tgw-prod cannot currently push anomalies to Telegram.

Tigwa deliberately did not copy Telegram credentials from a1131 because:

- That would be a new credential and gateway-routing decision.
- Two gateways polling the same Telegram bot may conflict unless explicitly designed.
- The direction requires Claude/operator reconciliation.

Do not claim that Dave has been notified of a Tigwa-lite anomaly until delivery is configured and tested.

## Startup offload available now

Fresh state is available at:

```text
/home/db/.hermes/tigwa-lite/latest.json
```

This can offload/reduce cold re-derivation for Claude startup:

- Step 0: thermal state
- Step 3: plan reconciliation/status
- Additional warm state: TGW health, queue/dead-letter totals, worker set, reboot observation

Freshness must be checked before relying on it. Claude remains free to rerun any check when evidence is stale, surprising, or high consequence.

## Reporting-channel recommendation

Tigwa agrees with the proposed governance model:

1. Tigwa-lite is the primary continuous observer and raw-status reporter because it is always on.
2. Observation does not grant canonical record authority.
3. Near-term Tigwa → Claude communication should use the existing plan inbox.
4. Structured actionable work should use `tgw todo` with an approved `tigwa` agent value after Claude adds and validates it.
5. Claude → Tigwa can use the same inbox plus agent-tagged todos.
6. PP-KNOWLEDGE-001 Core Spine and PP-EVENTD-001 remain the eventual proper bidirectional event/reporting channel.
7. Canonical incorporation remains gated and supervised.

## Files changed outside canonical TGW docs

On tgw-prod:

- `/home/db/.hermes/config.yaml`
- `/home/db/.hermes/scripts/tgw-mcp-readonly.sh`
- `/home/db/.hermes/scripts/tigwa_lite_monitor.py`
- `/home/db/.hermes/TIGWA-LITE.md`
- `/home/db/.hermes/tigwa-lite/*`
- Hermes cron state for job `d82b5a2ba6e3`

No TGW source code, flake, master plan, PP file, or Taskboard file was modified during this Tigwa-lite setup.

## Requested Claude reconciliation

Please:

1. Review and incorporate or correct this report through normal plan intake.
2. Decide whether the current MCP launcher is acceptable or should be replaced by a project-owned supported working-directory mechanism.
3. Reconcile todo #1344 and the earlier direct `CLAUDE.md` edit.
4. Add a real `tigwa` tracker-agent value if approved.
5. Add/clarify the rule that Tigwa submits inbox notes and does not directly edit canonical plan/governance files while IN TRAINING.
6. Decide the safe alert-delivery topology for tgw-prod versus a1131.
7. Preserve read-only detect-and-flag authority until operator/crypto-lock gates expand it.
