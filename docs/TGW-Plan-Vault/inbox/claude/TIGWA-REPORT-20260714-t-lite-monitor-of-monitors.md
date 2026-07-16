# TIGWA REPORT — T-Lite monitor-of-monitors installed

**From:** Tigwa / T-Lite setup pass  
**For:** Dave + Claude / TGW Plan Vault inbox  
**Date:** 2026-07-14 14:08 PDT  
**Status:** Implemented on a1131; KFMAWI alarm endpoint pending Tasker setup  

## Summary

Dave clarified that Tigwa Lite should use the DeepSeek reasoning model and act as a lightweight monitor-of-monitors: not another primary thermal authority, but a cheap secondary checker watching the existing monitors and local alarm plumbing.

Implemented a separate Hermes profile named `t-lite` with the friendly wrapper alias `t-liteful`.

T-Lite is configured as a DeepSeek-only profile using:

```yaml
model:
  provider: deepseek
  default: deepseek-reasoner
```

The profile `.env` contains only `DEEPSEEK_API_KEY`. No T-Lite `auth.json` remains after cleanup.

## Runtime state verified

At verification time:

```text
default Tigwa profile: running, model gpt-5.5
T-Lite profile:        running, model deepseek-reasoner, alias t-liteful
hermes-gateway.service:        active
hermes-gateway-t-lite.service: active
```

T-Lite has one active cron job:

```text
job_id:   6cdf332ce500
name:     t-lite-monitor-of-monitors
schedule: every 2m
mode:     no-agent script
script:   tgw_monitor_of_monitors.py
deliver:  local
last_run: ok / silent when healthy
```

## Files created or changed

T-Lite profile/config:

```text
/home/tigwa/.hermes/profiles/t-lite/config.yaml
/home/tigwa/.hermes/profiles/t-lite/.env
/home/tigwa/.hermes/profiles/t-lite/SOUL.md
/home/tigwa/.local/bin/t-liteful
```

Monitor-of-monitors script:

```text
/home/tigwa/.hermes/profiles/t-lite/scripts/tgw_monitor_of_monitors.py
```

KFMAWI alarm config:

```text
/home/tigwa/.hermes/tasker_alarm.json
```

Thermal sentinel repair:

```text
/home/tigwa/.hermes/scripts/tgw_prod_thermal_sentinel.py
```

## What the monitor-of-monitors checks

The T-Lite no-agent job is silent when healthy and emits a concise local report on state change/problem. It checks:

- default/full Tigwa gateway service is active,
- existing `temporary-tgw-prod-independent-watch` cron job exists/enabled/scheduled,
- existing `tgw-prod-thermal-sentinel-cheap` cron job exists/enabled/scheduled,
- tgw-prod reachability watch state is fresh,
- tgw-prod thermal sentinel state is fresh,
- tgw-prod is reachable according to the reachability state,
- tgw-prod thermal watchdog service reports active,
- thermal sentinel level is not elevated/problematic,
- local alarm target config names `KFMAWI`.

It does not mitigate, restart services, shut down hosts, edit production data, or alter canonical TGW state.

## KFMAWI alarm configuration

Dave identified `KFMAWI` as the local Android/Tasker alarm target.

Current config is intentionally disabled until TaskerNet / Tasker HTTP API is installed and the endpoint is known:

```json
{
  "enabled": false,
  "target_device": "KFMAWI",
  "role": "local informative alarm for TGW/tgw-prod monitor-of-monitors alerts",
  "url": "http://KFMAWI_STATIC_IP:PORT/tgw-alert",
  "token": "CHANGE-ME-LOCAL-RANDOM-TOKEN",
  "timeout_seconds": 5,
  "requires_ack": true
}
```

Once Dave installs the TaskerNet project and provides KFMAWI LAN IP/port/path/token, enable this config and run a labelled drill payload. The alarm is informational/ack-only and has no mitigation authority.

TaskerNet project to search/install:

```text
Tasker HTTP API
```

## Repair made during verification

The existing full Tigwa thermal sentinel had an old recorded cron-state problem:

```text
ssh-error: FileNotFoundError: [Errno 2] No such file or directory: 'ssh'
```

Root cause: cron environment did not reliably have `ssh` on PATH.

Fix: patched the sentinel to use:

```text
/run/current-system/sw/bin/ssh
```

Verification after patch:

```text
TGW thermal sentinel: NORMAL. watchdog=active, thermal.status=NORMAL|68C, age=5s.
```

After refreshing the thermal sentinel state, T-Lite monitor-of-monitors reported recovery/healthy, then subsequent cron run was silent as designed.

## Current limitations / next actions

1. KFMAWI Tasker endpoint is not enabled yet.
   - Dave will install TaskerNet `Tasker HTTP API`.
   - Need KFMAWI static/reserved LAN IP.
   - Need HTTP port/path.
   - Need shared local token.

2. T-Lite cron delivery is local only.
   - In CLI this means output is saved under the T-Lite cron output directory, not delivered into the current terminal.
   - This is acceptable for monitor-of-monitors state, but KFMAWI should become the human interrupt path.

3. T-Lite is monitoring monitors, not replacing them.
   - Existing tgw-prod thermal watchdog remains mitigation authority.
   - Existing full Tigwa / a1131 sentinel remains the richer alert/reasoning path.
   - T-Lite watches for monitor failure/staleness and alarm-target readiness.

## Verification evidence

Observed profile list:

```text
default  gpt-5.5            gateway running
t-lite   deepseek-reasoner  gateway running  alias t-liteful
```

Observed T-Lite cron:

```text
6cdf332ce500 [active]
Name:      t-lite-monitor-of-monitors
Schedule:  every 2m
Script:    tgw_monitor_of_monitors.py
Mode:      no-agent
Last run:  ok
```

DeepSeek reasoning smoke test returned:

```text
T-Lite reasoning online.
```

## Non-actions

This work did not:

- modify Dave's Nix flake,
- edit canonical TGW plan files directly,
- change production TGW data,
- add mitigation/shutdown authority,
- expose the Android alarm to WAN,
- or start any new agent during an incident.
