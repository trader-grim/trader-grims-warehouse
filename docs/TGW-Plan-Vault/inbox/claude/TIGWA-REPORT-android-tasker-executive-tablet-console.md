# TGW Android / Tasker Executive Tablet Console — Research and Recommendation

**To:** Claude  
**From:** Tigwa  
**Date:** 2026-07-15  
**Status:** Research/design recommendation only — no Android, Tasker, service, network, repository, or production-state change has been made.  
**Related plans:** `PP-TASKER-001`, `PP-HERMES-EA-001`, `PP-HARDWARE-001`; cross-reference `PP-INTAKE-002` Camera App proposal.  
**Related tracker:** todo #1425

## 1. Executive recommendation

Build a **TGW Tasker Edge Connector** and make the dedicated tablet in front of a1131 its first reference node: an always-on, human-facing executive console and local alarm/acknowledgement appliance.

Do **not** begin by building a new all-purpose Android application or giving an agent arbitrary control of Tasker/Termux. TGW already has a mature Android/Tasker estate: the CameraData enhancement lineage began in 2014; Tasker runs camera-data collectors; multiple tablets and camera/collector devices exist; and purpose-built picker tools already provide pick-list, magnifier, and flashlight workflows. The right next move is to document and expose this proven operational estate through a small, typed, audited capability interface.

The proposed console should have three jobs:

1. **Interrupt:** locally alarm and display incidents independently of cloud messaging.
2. **Orient:** provide a glanceable, read-only view of health, queue/review state, and device status.
3. **Acknowledge/route:** let Dave explicitly acknowledge an incident or request a bounded, pre-approved follow-up. It must not become an autonomous control plane.

The implementation should be local-LAN-first, with Tasker as the device automation/runtime, Termux/Termux:API as the optional edge-script/client layer, and a small a1131-side bridge as the policy/audit boundary. Join, KDE Connect, and Syncthing each have useful supporting roles, but none should be the critical-path alarm authority.

## 2. What is already known (do not overwrite with assumptions)

Verified TGW context:

- TGW is an Android-heavy operation, not a desktop system with incidental phones.
- Tasker runs the existing CameraData collector workflow.
- The first Tasker camera enhancement was made in 2014.
- Multiple Android tablets and camera/collector devices are in service.
- A picker tool already has a constrained warehouse interface: pick list, magnifier, and flashlight.
- Several devices already have Termux and Termux:API.
- The selected alarm/console tablet is physically in front of a1131.
- Dave owns Tasker, AutoTools, several plugins, and a Join license.
- Syncthing is TGW's informal durable data plane; `/home/db/Sync` is a delivery location. It is not a real-time command channel.
- KDE Connect is already available for local device/desktop handoff.

Unknown until source/export and device inventory are supplied:

- actual Tasker project/profile/task/scene names and exports;
- installed AutoApps plugin list and versions;
- Android versions, device identifiers, device-owner/kiosk status, and battery policies;
- existing camera/scanner/Foldio/other app intents and contracts;
- network segmentation, static leases, and permitted listener ports;
- existing TaskerNet imports and locally modified copies.

Claude should treat all source-level claims in this report as recommendations, not assertions about the current estate.

## 3. Architecture decision

### 3.1 Use a capability contract, not remote UI automation

Expose named capabilities, typed payloads, and explicit authority levels. The agent or a1131 bridge requests a known capability; the tablet's Tasker project maps it to local Tasks/Scenes. The requester does not receive arbitrary shell execution, arbitrary Tasker task names, accessibility clicks, or unrestricted device control.

```text
a1131 policy/audit bridge
  |  authenticated, typed LAN request
  v
KFMAWI Tasker Edge Connector
  |-- Tasker profiles/tasks/scenes
  |-- AutoNotification / AutoTools where justified
  |-- Termux / Termux:API optional local adapter
  v
Android alarm, screen, TTS, vibration, flashlight, and named local workflows
```

Example registry record:

```yaml
capability_id: kfmawi.alarm.raise
role: executive_console
request_schema:
  event_id: string
  severity: [info, warning, critical]
  title: string
  message: string
  requires_ack: boolean
  expires_at: rfc3339
response_schema:
  delivery_id: string
  device_id: string
  accepted_at: rfc3339
authority:
  - alert presentation only
  - no production mitigation
  - no arbitrary task execution
```

Initial capability set:

```text
kfmawi.health.read
kfmawi.status.show
kfmawi.alarm.raise
kfmawi.alarm.ack
kfmawi.self_test.run
kfmawi.console.open_review
```

`ack` means *a human saw/acknowledged the incident*. It never means a mitigation, production mutation, or human approval occurred unless a separate named workflow records that approval.

### 3.2 Transport choice

**Primary:** a1131-side local LAN gateway with per-device credentials, timestamp/nonce, event ID, expiry, and idempotency. Prefer Tasker-originated HTTPS requests to that gateway for authoritative status, ACK, and commands: the gateway owns authorization, schema validation, replay protection, and the canonical audit record. Tasker's HTTP Request action supports body/file submissions and multipart forms.

Tasker also documents a local HTTP Request event for receiving requests. Treat that as a narrowly scoped local adapter/testing option, not the preferred authority boundary: it must not be WAN-exposed and should accept only a strict authenticated, replay-protected allowlist. Use explicit/package-scoped intents where possible and validate all extras; generic implicit intents are not a command-security boundary.

**Tablet local runtime:** Tasker receives the approved payload and renders/acts on it. Termux may run supporting scripts or produce richer device-health information through Termux:API. Keep the adapter small and observable.

**Do not use as primary transport:**

- **Syncthing:** its watcher/full-scan model is durable asynchronous synchronization, not a deadline-sensitive command/ack bus.
- **Join:** valuable cross-device/away-from-LAN fallback, but it introduces a cloud-mediated dependency.
- **KDE Connect:** valuable LAN-local convenience and manually initiated predefined commands, but it is not the canonical alarm/audit transport.
- **AutoInput:** useful only as a last-resort compatibility adapter; screen coordinates/accessibility UI are brittle and high-authority.
- **AutoRemote/MQTT:** viable optional adapters after a reliability/security test, not prerequisites for v1.

## 4. Executive console design

### 4.1 Normal glance screen

Use a deliberately sparse display, large type, color plus text/icon status, and one-tap navigation. No scrolling dashboard should be required to answer "is something wrong?".

```text
┌───────────────────────────────────────────────┐
│ TGW EXECUTIVE CONSOLE       10:42  LOCAL/LAN  │
├───────────────────────────────────────────────┤
│ INCIDENT: NONE                                │
│ a1131: OK       tgw-prod: OK       tablet: OK │
│ local network: OK   power/charge: OK          │
├───────────────────────────────────────────────┤
│ Operations                                     │
│  • active review requests: N                   │
│  • Tigwa queue / current work: concise state   │
│  • CameraData fleet: available / attention     │
├───────────────────────────────────────────────┤
│ [STATUS] [REVIEWS] [SELF-TEST] [HELP]          │
└───────────────────────────────────────────────┘
```

The console should show links/IDs and concise state, not replicate an entire management application. Deep detail belongs in the canonical TGW system/Plan Vault or a dedicated web page opened intentionally.

### 4.2 Critical incident mode

When an accepted `alarm.raise` request has `requires_ack: true`:

- wake/brighten the screen as the device permits;
- show severity, source, event ID, time, concise facts, and the required human action;
- play sound and TTS at the preconfigured emergency level; use vibration/flashlight only where operationally appropriate;
- repeat on a documented interval until ACK or expiry;
- provide explicit **ACKNOWLEDGE** and **SHOW DETAILS** actions;
- POST a signed acknowledgement with event ID, device ID, and timestamp to a1131;
- preserve the tablet-local event/ack log long enough to reconcile after a network interruption.

The earlier TGW annunciator proposal already defines a useful minimum sequence: `TGW_ALERT_RECEIVE`, `TGW_ALARM_LOOP`, `TGW_ACK`, `TGW_ANDROID_HEARTBEAT`, and `TGW_SELF_TEST`. Retain that vocabulary unless actual exports show a better established naming convention.

### 4.3 Review/approval mode

The tablet may present review requests, but the action vocabulary must mirror the governance loop:

```text
view_request
open_canonical_artifact
acknowledge_seen
request_clarification
submit_human_decision
```

Do not put "approve and execute" buttons on the console unless the particular workflow has an explicit PP/runbook authorization, an immutable request ID, and a durable human decision record. The human remains the gate; the tablet is a clearer interface to that gate.

## 5. Tasker and TaskerNet assessment

### Tasker core — recommend as the runtime

Tasker remains the correct device-local orchestrator because its existing estate already embodies TGW operations. Relevant documented primitives include:

- **Scenes:** Tasker supports scene creation/show/hide/destroy plus scene elements and element event tasks. Use this for a first native console/alarm UI rather than immediately introducing another framework.
- **HTTP Request:** Tasker has an HTTP request action and supports bodies/files. Use it for bounded requests, acknowledgements, and heartbeats.
- **Intents:** Tasker documents Android intent send/receive behavior. Use intents for local app/plugin integration when an app publishes a stable contract.
- **TaskerNet:** TaskerNet is the official public list of Tasker projects from the developer and community. It is valuable as a pattern/source library, not as a trusted production dependency.

TaskerNet import rule: import into an isolated test device/project, inspect every profile/task/variable/plugin permission/network endpoint, redact any embedded endpoints or tokens, then recreate or adapt the useful pattern in TGW-owned source. Never treat a shared TaskerNet project as reviewed operational code.

### Tasker project structure recommendation

Once Dave supplies the source tree, Claude should preserve it first, then add a separate TGW-owned project namespace rather than refactor old CameraData logic in place:

```text
TGW_EDGE_CORE
  TGW_EDGE_RECEIVE
  TGW_EDGE_VALIDATE
  TGW_EDGE_HEARTBEAT
  TGW_EDGE_LOG_LOCAL

TGW_EXECUTIVE_CONSOLE
  TGW_CONSOLE_HOME
  TGW_CONSOLE_INCIDENT
  TGW_CONSOLE_REVIEW
  TGW_CONSOLE_SELF_TEST

TGW_CAMERA_ADAPTER        # only after inventory confirms contracts
TGW_PICKER_ADAPTER        # only after inventory confirms contracts
```

This keeps legacy CameraData workflows stable while giving the connector an independent lifecycle and test surface.

## 6. AutoApps / related-app assessment

| Component | Recommendation | Purpose and constraints |
|---|---|---|
| **AutoTools** | **Adopt selectively** | Strong candidate for richer Tasker UI/variable/display helpers and web-screen-style rendering. Use only if a Tasker Scene cannot meet the console UI cleanly. Prefer a small, static, offline-capable interface over a complex browser dashboard. |
| **AutoNotification** | **Adopt** | Useful for creating/managing actionable Android notifications and observing selected notification events. Use it for local incident surface and button actions; tightly filter listened packages/categories and do not turn notification scraping into a hidden control plane. |
| **AutoInput** | **Avoid for core flow; compatibility-only** | It can interact with other Android apps and read UI through accessibility. That makes it useful for temporary legacy integration, but changes in layout/locale/permissions can silently break it. Never make an alert ACK, inventory mutation, or safety operation depend on UI clicking. |
| **AutoVoice** | **Exclude from v1; reconsider only with a separate voice design** | The developer documents the loss of third-party Google Assistant services. Voice recognition may still be possible through other paths, but it is not a reliable executive-console safety/control path. |
| **AutoRemote** | **Evaluate as a secondary adapter** | Its message/reaction model can be useful inside the Android/desktop estate. Prefer the typed local gateway for the authoritative path; use AutoRemote only after measured LAN/reboot/offline testing. Do not use its file-sharing feature for sensitive material. |
| **Join** | **Adopt as supplemental transport** | Official Join supports Android, desktop, web and API surfaces. It is useful for cross-device notifications, URLs/files, and away-from-LAN relay. Keep it out of the sole critical-alarm path and keep its API credentials out of Tasker exports/Git. |
| **AutoShare** | **Optional** | Good for explicit user-driven Android Share-sheet handoff of a URL, photo, or text into a named TGW intake/review workflow. It is not a core console dependency. |
| **AutoBarcode** | **Evaluate only after scanner inventory** | The estate already has a commercial scanner app and camera workflows. First audit its published intents/output. Add another barcode layer only if it solves a demonstrated gap. |
| **AutoLocation / AutoContacts / AutoWear / AutoCast / AutoAlarm / AutoVera / AutoArduino / AutoLaunch** | **Out of scope for v1** | Do not add plugin surface area without a named operating use case. Reconsider AutoLaunch only if the dedicated console needs a robust, tested app-recovery/kiosk behavior. |

The publisher's current AutoApps site lists the above ecosystem and also identifies a "Delisted App Archive." That is a reason to keep dependencies minimal, version-pinned in a manifest, and backed by TGW-owned exports/docs—not a reason to reject the useful maintained components.

## 7. Termux, KDE Connect, Syncthing, Join, and kiosk options

### Termux + Termux:API — recommend as the edge utility layer

The official `termux-api` project describes itself as an add-on exposing device functionality to command-line programs. Use it to augment Tasker, not replace it:

- health collection: battery, charging, network, storage, selected sensor state;
- locally queued, signed HTTP requests/acknowledgements;
- diagnostics invoked only by a named console self-test;
- optional TTS/notification/media operations where more controllable than a Tasker action;
- an adapter process with a small explicit allow-list.

`Termux:Tasker` is a strong bounded bridge when Tasker must invoke a local script. Keep reviewed scripts under `~/.termux/tasker/`; do **not** enable broad external absolute-path execution or `allow-external-apps`, because its own documentation warns of the arbitrary-command risk. Use `Termux:Boot` and supervised Termux services only after reboot/Doze/battery-optimization testing proves recovery behavior on the actual tablet.

Do not give Hermes a general Termux shell. Any script invoked by Tasker should be versioned, named, input-validated, and logged.

### KDE Connect — recommend for LAN-local convenience

KDE Connect officially provides cross-device files/links, notification relay, battery visibility, remote controls, and predefined desktop commands. Use it for:

- manual file/URL/clipboard handoff;
- Dave-initiated predefined desktop actions;
- device presence/battery context;
- a convenient local secondary notification surface.

Do not use KDE Connect's arbitrary command capability as an unbounded agent-to-desktop execution channel. Define only reviewed, read-only or safe commands and record their invocation.

### Syncthing — retain as durable asynchronous evidence plane

The official documentation describes watcher and regular full-scan detection, and explicitly notes regular scans remain advisable even with watching enabled. That is excellent for source exports, screenshots, report delivery, result manifests, offline capture queues, and audit artifacts. It is unsuitable as the only path for immediate alarms, approvals, or ACK round trips.

### Join — licensed, useful, but not an emergency dependency

Join's official site exposes Android, desktop, web, and API surfaces. Use it as a cross-network notification and command supplement, particularly for non-LAN devices and human-facing messages. Treat it as a convenience/fallback transport because its connectivity and account/API dependency are outside the local alarm path.

### Fully Kiosk Browser and Android dedicated-device mode — evaluate after v1

For a dedicated unattended tablet, Fully Kiosk Browser is a credible later option: its official documentation advertises fullscreen browser/app launcher behavior, kiosk restriction, remote admin, and device-management features without root. More importantly, Android's official dedicated-device / COSU model is the correct device-level security boundary when the tablet is promoted into a controlled single-purpose console. A full-screen Tasker Scene alone is not kiosk security.

Do not add a new paid/managed dependency before proving that Tasker Scenes/AutoTools plus the tablet's supported dedicated-device policy cannot meet the v1 requirements. A later local PWA served by a1131 is a good read-mostly dashboard candidate; Tasker remains the local alarm/action adapter.

## 8. Recommended build sequence

### Phase 0 — source preservation and inventory (Claude, after Dave moves source)

1. Create the new Git repository only after the source tree is placed in the TGW ecosystem.
2. Preserve an untouched import snapshot and produce a SHA-256 manifest before edits.
3. Identify Tasker exports, plugin/package dependencies, local assets, Termux scripts, scanner/camera integrations, and any device-specific instructions.
4. Remove/redact secrets from tracked exports and create a separate `[REDACTED]` configuration template.
5. Produce a machine-readable inventory and a human-readable lineage/map. Do not normalize or rewrite the 2014-era workflows during this phase.

Suggested repository shape:

```text
android-tasker-edge/
  README.md
  docs/
    architecture.md
    device-registry.yaml
    capability-contract.yaml
    dependency-manifest.yaml
    runbooks/
  tasker/
    imported-original/          # preserved, review-only baseline
    tgw-edge-core/
    tgw-executive-console/
  termux/
    scripts/
    config.example/
  tests/
    fixtures/
    acceptance-drills.md
  evidence/
    import-manifest.sha256
```

### Phase 1 — auditable, local alarm/ACK vertical slice

Implement only on the tablet in front of a1131:

1. authenticated typed alert receive;
2. full-screen audible/visible incident scene;
3. event-ID-bound acknowledgement to a1131;
4. every-1-to-5-minute tablet heartbeat;
5. manual self-test; and
6. a local event log plus a canonical a1131 record.

Run acceptance drills with internet disabled but LAN available, device on battery, screen locked/off where applicable, a1131 reboot, tablet reboot, duplicate event delivery, expired event, and ACK retry. Nothing is operationally trusted until these are evidenced.

### Phase 2 — read-only executive dashboard

Add a compact status card that retrieves only approved summaries: host health, current incident, current queue/review state, and fleet availability. The console must degrade clearly to **STALE/UNKNOWN**, never imply green because a request failed.

### Phase 3 — Android fleet interfaces

After the real exports are inventoried:

- document CameraData collector events, queue semantics, camera/scanner app boundaries, and failure recovery;
- add a collector device heartbeat/status contract;
- document picker device capability contracts, preserving its constrained operator workflow;
- add device registry entries and versioned dependency manifests;
- keep each device role independently deployable/testable.

### Phase 4 — optional transports/features

Only after Phase 1–3 drills hold up: Join fallback, KDE Connect convenience commands, voice grammar, kiosk management, optional AutoRemote/MQTT adapter, or richer AutoTools UI.

## 9. Safety, governance, and observability requirements

- Per-device credential; no shared fleet-wide secret in Tasker exports.
- Timestamp, nonce, event ID, expiry, replay rejection, idempotent delivery, and explicit schema version on requests.
- LAN-only listener for v1; no WAN exposure.
- Capability allow-list in both the a1131 bridge and tablet runtime.
- No arbitrary Termux shell, Android intent, Tasker task, accessibility click, or desktop command.
- Every alert, ACK, self-test, and failed validation emits an auditable record with a correlation ID.
- Health distinguishes `OK`, `DEGRADED`, `STALE`, and `UNKNOWN`.
- Syncthing carries durable artifacts/exports/evidence, not command authority.
- TaskerNet/third-party imports are reviewed as untrusted code.
- Human approval remains a separate explicit event; device acknowledgement is not approval.
- The six-stage TGW governance loop applies: request → document → trigger → accomplish → independent review → human sign-off.

## 10. Acceptance criteria before operational promotion

1. The console alarms locally with internet unavailable and LAN intact.
2. It alarms when a cloud service, Join, KDE Connect, or Telegram is unavailable.
3. A critical alert appears from locked/screen-off state as device policy permits, or the limitation is documented and mitigated.
4. ACK binds to exactly one event ID and appears in the a1131 audit record.
5. Duplicate/out-of-order/expired requests cannot create ambiguous state.
6. Tablet reboot recovers the runtime and emits a fresh heartbeat.
7. a1131 cannot silently interpret missing tablet health as healthy.
8. The self-test creates clearly labelled test evidence and cannot be mistaken for a live incident.
9. Source, configuration template, dependency manifest, and drill evidence are in the new repository with secrets excluded.
10. Dave reviews the visual ergonomics, audible alarm, physical placement, and authority boundary before the console is relied upon.

## 11. Decisions requested from Dave / Claude

**Dave**

- Confirm the designated tablet/device identity and physical/audio expectations.
- Choose whether the v1 visual surface should be Tasker Scene first or an existing local web UI launched/kiosked by Tasker.
- Define alarm sound, repeat interval, quiet-hours exception policy, and what constitutes an ACK.
- Confirm whether console review actions are display/route-only in v1 (recommended).

**Claude**

- After source-tree placement, create the independent Git repository and import manifest.
- Inventory real exports before making modernization decisions.
- Reconcile legacy CameraData behavior with `PP-TASKER-001` and this recommendation.
- Propose the smallest tested a1131 bridge and Tasker project change set; do not modify production workflows before review.
- Produce a machine-readable device registry and capability contract alongside human-readable architecture/runbooks.

## 12. Source review

Primary/current sources reviewed on 2026-07-15:

- Tasker Scenes: https://tasker.joaoapps.com/userguide/en/scenes.html
- Tasker HTTP Request action: https://tasker.joaoapps.com/userguide/en/help/ah_http_request.html
- Tasker Intents: https://tasker.joaoapps.com/userguide/en/intents.html
- TaskerNet official project catalogue: https://taskernet.com/
- AutoTools: https://joaoapps.com/autotools/
- AutoNotification: https://joaoapps.com/autonotification/
- AutoInput: https://joaoapps.com/autoinput/
- AutoRemote: https://joaoapps.com/autoremote/
- Join: https://joaoapps.com/join/
- Termux:API: https://github.com/termux/termux-api
- KDE Connect: https://kdeconnect.kde.org/ and https://userbase.kde.org/KDEConnect
- Syncthing synchronization model: https://docs.syncthing.net/users/syncing.html
- Fully Kiosk Browser: https://www.fully-kiosk.com/en/#features
- Android dedicated devices / COSU: https://developer.android.com/work/dpc/dedicated-devices
- Termux:Tasker security and script bridge: https://github.com/termux/termux-tasker
- Termux:Boot: https://github.com/termux/termux-boot
- Termux services: https://github.com/termux/termux-services
- Tasker local HTTP Request event: https://tasker.joaoapps.com/userguide/en/help/eh_http_request.html
- Tasker plugin security note: https://tasker.joaoapps.com/userguide/en/help/ah_plugin.html
- Tasker encryption caveat: https://tasker.joaoapps.com/userguide/en/encryption.html

TGW source context reviewed:

- `docs/TGW-Plan-Vault/plan/pp/PP-TASKER-001.md`
- `docs/TGW-Plan-Vault/inbox/DONE-1375-android-tasker-proposal-filed.md`
- `docs/TGW-Plan-Vault/reference/PP-INTAKE-002-camera-app-design.md`

## 13. Non-actions

This report does not create a repository, import or modify the supplied source tree, configure Tasker/AutoApps/Termux, alter a1131/tgw-prod services, modify the Nix flake, change Android permissions, or make production data changes. It is a review/request packet for the next bounded, human-gated step.
