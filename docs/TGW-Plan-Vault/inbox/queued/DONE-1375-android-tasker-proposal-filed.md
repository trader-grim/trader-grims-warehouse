# Proposal: Android/Tasker Emergency Annunciator for TGW local alerts

**From:** Tigwa / Leotha  
**For:** Dave + Claude review  
**Date:** 2026-07-13 18:30 PDT  
**Status:** Proposal only — no production/config/router changes made  
**Candidate parent plan:** PP-HARDWARE-001 / DIR-868L router ecosystem (#1232), with cross-link to PP-HERMES-EA-001 and future thermal/drill runbook

## Summary

Dave has multiple older Android devices already used in the TGW camera/Tasker ecosystem. One can be dedicated as a local, battery-backed emergency annunciator for tgw-prod/Tigwa-lite alerts.

This should be treated as a **human alarm and acknowledgement appliance**, not the canonical event bus and not a source of business truth.

The simplest v1 trigger is a local LAN HTTP POST from a1131's independent watchdog or tgw-prod/Tigwa-lite to Tasker/AutoTools/AutoRemote/Join on the dedicated Android device.

No Telegram, cloud service, LLM, NATS, router firmware change, or flake change is required for v1.

## Why this exists

The 2026-07-13 tgw-prod thermal incident exposed a gap:

- The local 88°C shutdown mitigation worked.
- Tigwa's independent monitoring/parallel-response path initially failed.
- Telegram delivery later worked, but Dave correctly objected to relying on an outside service for emergency alerting.
- A local audible/visible human alarm with acknowledgement is needed.

An older Android device is a good fit because it is:

- low power,
- battery backed,
- already familiar in TGW's Android/Tasker workflow,
- loud enough to wake/interrupt a human,
- screen-capable for a persistent incident panel,
- sensor-rich,
- controllable by Tasker/AutoTools/Join,
- and cheap enough to dedicate.

## Proposed role

Use the dedicated Android as:

- local siren,
- TTS announcer,
- full-screen incident panel,
- acknowledgement button,
- heartbeat source,
- local fallback bridge,
- optional SMS/call/Join fallback,
- and secondary witness of LAN health.

Do **not** use it as:

- the only durable event log,
- canonical queue/state storage,
- a TGW business-state authority,
- or the only watchdog.

## V1 architecture — direct local HTTP

```text
a1131 independent watchdog
  or tgw-prod / Tigwa-lite thermal script
      |
      | HTTP POST on local LAN
      v
Dedicated Android Tasker listener
      |
      +--> loud alarm / TTS / vibration / screen / flashlight
      +--> Dave ACK button
      |
      | HTTP POST ack back to a1131
      v
a1131 writes incident/ack record and wakes full Tigwa if needed
```

This gives a working local alarm before the router/NATS decision is finished.

## V2 architecture — local bus bridge

If/when the DIR-868L or another low-power host runs a local broker:

```text
tgw-prod / Tigwa-lite
  -> immutable text outbox
  -> NATS JetStream or MQTT local bus
       +-> Android Tasker annunciator
       +-> a1131 Hermes webhook bridge
       +-> desktop/local alarm
       +-> Telegram/offsite fallback
```

Android should still receive a simple HTTP/Tasker command from a bridge. It should not need to speak JetStream directly.

## V3 architecture — finished emergency fabric

```text
Producers:
  - tgw-prod / Tigwa-lite
  - a1131 independent watchdog
  - router health probe
  - Android heartbeat

Durability:
  - immutable text outbox per producer
  - optional JetStream persistence/replay

Human interrupt:
  - dedicated Android Tasker alarm
  - a1131 desktop/audio alarm
  - offsite fallback only after local paths

Reasoning:
  - full Tigwa via Hermes webhook/session
  - Claude inbox/runbook reconciliation
```

## Tasker profiles/tasks

### 1. TGW_ALERT_RECEIVE

Trigger: local HTTP request, AutoRemote command, Join command, or plugin-specific equivalent.

Inputs:

```json
{
  "event_id": "uuid-or-stamp",
  "severity": "critical",
  "host": "tgw-prod",
  "component": "nvme0n1",
  "sensor": "temperature_sensor_1",
  "temperature_c": 87,
  "threshold_c": 88,
  "message": "tgw-prod SSD is 87 C, one degree below shutdown",
  "requires_ack": true
}
```

Actions:

- Validate shared token.
- Store event fields in Tasker variables.
- Set `%TGW_ACKED = false`.
- Start `TGW_ALARM_LOOP`.

### 2. TGW_ALARM_LOOP

Actions:

- Set alarm/media volume high.
- Speak the event message using Android TTS.
- Play siren sound.
- Vibrate if available.
- Flash screen/flashlight if appropriate.
- Show full-screen scene with current facts.
- Repeat every 30–60 seconds until acknowledged or explicitly silenced.

### 3. TGW_ACK

Trigger: scene button or dedicated quick action.

Actions:

- Stop siren loop.
- Record acknowledgement timestamp.
- HTTP POST acknowledgement back to a1131:

```json
{
  "event_id": "same-event-id",
  "acknowledged_by": "Dave",
  "acknowledged_at": "device-local-timestamp",
  "device": "android-monitor"
}
```

### 4. TGW_ANDROID_HEARTBEAT

Schedule: every 1–5 minutes.

Report to a1131:

- device alive,
- battery percent,
- charging state,
- Wi-Fi SSID,
- IP address,
- Tasker profile enabled state if available,
- last alert received,
- last acknowledgement sent.

### 5. TGW_SELF_TEST

Manual drill button.

Actions:

- Simulate a critical alert.
- Verify sound, TTS, screen, ack path, and incident logging.
- Mark as drill/test in payload.

## Candidate trigger mechanisms

### Preferred v1: local HTTP listener

Use whichever existing Android tool gives the simplest reliable local endpoint:

- Tasker HTTP-capable plugin,
- AutoTools web/HTTP capability,
- AutoRemote,
- Join command/API path if it can be kept local enough,
- or a tiny local Android helper app if plugins prove unreliable.

The producer sends:

```text
POST http://ANDROID_STATIC_IP:PORT/tgw-alert?token=<random-local-token>
```

### Fallbacks

- Join command trigger, if acceptable.
- AutoRemote command trigger.
- MQTT plugin subscription if MQTT is selected before JetStream.
- Syncthing drop-file trigger for evidence/secondary path only.
- SMS/call/Join/Telegram only as offsite fallback.

## Security and safety envelope

- LAN-only.
- Static DHCP reservation for the Android device.
- Random shared secret/token.
- Do not expose the Android listener to WAN.
- Alert payloads should contain operational facts, not secrets.
- No authority to shut down tgw-prod.
- Android ACK means “Dave saw the alert,” not “mitigation approved.”
- Any destructive or workload-pausing action still requires Dave's explicit direction unless separately authorized by runbook.

## Acceptance tests

Before relying on the device:

1. Send a test alert from a1131 to Android.
2. Confirm audible alarm and TTS are loud enough from expected locations.
3. Confirm full-screen scene appears even when screen is off/locked, or document the limitation.
4. Confirm ACK button posts back to a1131.
5. Confirm missed ACK repeats until acknowledged.
6. Unplug Android power and verify battery alert/heartbeat reports the change.
7. Turn off internet but keep LAN Wi-Fi up; verify local alert still works.
8. Reboot Android; verify Tasker profile resumes automatically.
9. Reboot a1131; verify Android heartbeat is visible afterward.
10. Simulate tgw-prod offline; verify independent watcher alerts Android.
11. Run a labelled drill monthly or after major changes.

## Relationship to NATS JetStream / DIR-868L

This proposal complements the DIR-868L/NATS discussion; it does not replace it.

- Direct HTTP to Android is the smallest useful local alarm.
- NATS JetStream remains a candidate local emergency bus when replay/fan-out/ack are needed.
- The Android should be a subscriber/annunciator, not the JetStream authority.
- If the DIR-868L runs JetStream, a small bridge should translate bus events into Android HTTP/Tasker commands.
- If MQTT is selected for Android convenience, preserve the same producer-side immutable text outbox so the broker is not the only record.

## Open decisions for Dave

- Which Android device to dedicate.
- Which plugin path is most reliable on that device: Tasker direct HTTP, AutoTools, AutoRemote, Join, MQTT plugin, or tiny helper app.
- Where the device physically lives so the alarm is heard.
- Whether it should also send SMS/call/Join fallback.
- Whether it should be part of the camera/intake device VLAN or a separate monitor lease.
- Alarm tone, repeat interval, and acknowledgement rules.

## Proposed plan insertion

Add a child item under PP-HARDWARE-001 / router ecosystem or PP-HERMES-EA-001:

**Android/Tasker emergency annunciator:** dedicate one existing Android/Tasker device as a battery-backed local alarm/ACK panel for tgw-prod/Tigwa-lite incidents. V1 direct LAN HTTP from a1131 watchdog; V2 subscribe through local broker bridge. Acceptance: internet disconnected, alert still produces local audible alarm and ACK round-trip.

## Non-actions

This proposal did not:

- change router firmware,
- configure Android,
- modify Tasker profiles,
- edit canonical plan files,
- modify the Nix flake,
- change tgw-prod services,
- or alter production data.
