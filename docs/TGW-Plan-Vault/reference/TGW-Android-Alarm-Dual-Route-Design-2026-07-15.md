# Android alarm dual-route design

**Tracker:** #1385  
**PP:** PP-HERMES-EA-001  
**Decision by:** Dave, 2026-07-15  
**Status:** approved design direction; no device/server configuration changed

## Decision

Use the existing Tasker HTTP API as the primary alarm route. Add one KDE Connect-based route as an independent delivery leg:

```text
primary:   HTTP API → Tasker alarm adapter
secondary: KDE Connect remote command when a fixed device command is exposed
fallback:  KDE Connect explicit clipboard envelope when remote command is unavailable
```

The primary and secondary routes must carry the same incident ID. Tasker must deduplicate the event, so dual delivery results in one alarm presentation and a merged route receipt.

## Why

The HTTP API is useful for typed request/response and status/ack semantics, but its post-reboot ADB permission requirement makes it insufficient as the only alarm leg. KDE Connect avoids that particular dependency but has separate Android boot, network, pairing, and background-execution risks. Dual routing improves availability without granting broad control.

## Required command contract

Only a fixed alarm adapter may be invoked. Do not let either transport name arbitrary Tasker tasks.

```yaml
schema: tgw.alarm.v1
operation: raise | test
incident_id: UUID-or-stable-event-id
severity: critical | elevated
issued_at: ISO-8601 UTC
expires_at: ISO-8601 UTC
nonce: opaque-per-event
source: tgw-monitor
```

The adapter maps `raise` to the single approved Tasker alarm task. It rejects unknown fields/operations, expired events, duplicate incident IDs, and malformed payloads. `clear`, task execution, shell, URLs, and generic intents are out of scope.

## Route policy

| Event class | HTTP API | KDE route | expected behavior |
|---|---|---|---|
| routine/healthy | no Android alarm | none | no device interruption |
| elevated/critical | send primary | send secondary with same incident ID | Tasker deduplicates; one alarm display; record both receipts |
| HTTP explicit permission/transport failure | record failure | send KDE route immediately | no attempt to automate ADB permission repair |
| KDE remote-command unavailable | primary remains attempted | explicit D-Bus clipboard fallback | transport acceptance is not device completion |

KDE Connect remote commands are preferred over clipboard only after KFMAWI exposes a fixed, named command for this adapter and its behavior is tested. Current a1131 inspection found no exposed KFMAWI remote commands; this is a configuration/test prerequisite, not a claim that the route is ready.

For clipboard fallback, use the proven `db` desktop-session KDE Connect D-Bus method `sendClipboard(explicit_text)`. Do **not** use `kdeconnect-cli --send-clipboard`: it sends the current desktop clipboard rather than an explicit payload.

## Receipts and health

Separate these states:

```text
transport_accepted     KDE/HTTP accepted a request
adapter_received       Tasker parsed an allowed event
alarm_presented        local alarm task started/shown
acknowledged           Dave or configured device workflow acknowledged incident
```

The monitor must report route-specific failure, not pretend that dispatch proves an alarm happened. After a KFMAWI reboot, an HTTP permission error should mark HTTP degraded and prompt the human ADB re-grant; it must not be repaired automatically.

## Promotion test

1. Create a non-audible `test` presentation with an identifiable incident ID.
2. Test HTTP-only receipt chain.
3. Test fixed KDE remote command once exposed; otherwise test explicit D-Bus clipboard fallback.
4. Test parallel delivery of the same ID and verify exactly one local presentation.
5. Reboot KFMAWI without restoring the HTTP API ADB permission.
6. Verify HTTP reports degraded, KDE reaches the Tasker adapter after normal boot/network recovery, and the device-side receipt is captured.
7. Verify Tasker/KDE Connect battery/autostart behavior and a human-visible recovery procedure.

No real incident alarm or device setting should be changed before Dave approves the tested adapter details.
