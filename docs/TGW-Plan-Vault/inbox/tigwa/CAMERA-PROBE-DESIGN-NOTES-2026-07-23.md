# Camera probe — design notes and research fixtures

**Date:** 2026-07-23  
**Status:** Dave-directed working notes for the camera/data-collection capability. Staged material, not a canonical implementation specification or authority to modify production data/hardware.

## Product intent

The camera application is a standalone-first data-collection probe with a two-way event surface.

```text
Command/intent ingress
  → validate the existing JSON command
  → bounded local camera/probe action

Collection/health/result egress
  → durable update packet / local outbox
  → existing HUD
  → selected upstream pipeline consumer when connected
```

It must remain useful without a network, upstream system, Tasker, Linux bridge, HUD connection, or attached scanner/turntable.

## Operator workflow

The operator experience should stay small.

```text
Choose NEW ITEM or EXISTING SKU
→ take one or a few required pictures
→ enter a few measurements
→ Done
```

### New item

```text
Start new package
→ capture photos and measurements
→ complete local staged intake/update packet
```

### Existing SKU

```text
Scan or select existing SKU
→ capture a new photo or small photo set
→ record measurements
→ complete local update packet linked to the SKU
```

The same probe supports creation and enrichment. It is not two separate apps.

## Update-packet contract

“Done” creates an **update packet**; it does not directly overwrite catalog truth.

```text
capture complete
→ durable local update packet + photo/measurement manifest
→ connected: send immediately
→ disconnected: retain in local outbox and forward on reconnection
→ upstream receipt/acknowledgement
→ existing pipeline validates, reviews, and applies under policy
```

Minimum reserved fields:

```yaml
packet_id: stable idempotency identity
schema_version:
target: new-item | existing-sku
sku: null-or-target-SKU
intent: create | enrich | update
captured_at:
probe_context:
measurements: []
media: []
delivery_state: captured | queued | sent | acknowledged | rejected | failed
extensions: {}
```

`extensions` may contain additional key/attribute pairs without requiring an app release for every new evidence field. Keep extension keys namespaced or template-defined so downstream consumers can interpret them.

The local source material remains recoverable until upstream receipt is acknowledged. Retries/resends must use the same stable packet identity and not silently create duplicate updates.

## Existing Android control vocabulary

The camera app should join Dave’s existing small control surface, not introduce a second command language or dashboard.

```text
Existing JSON command / remote trigger
→ validated camera-app adapter
→ explicit success/failure/state event
→ existing HUD update
```

Before changing the app, capture representative real command payloads and HUD states. Record command names, required fields, authority/authentication expectation, idempotency behavior, expected result, and expected HUD state.

Minimum end-to-end proof:

```text
1. Harmless existing remote JSON trigger reaches Android control layer.
2. Adapter accepts a valid recognized command and visibly rejects malformed,
   duplicate, or unauthorized inputs.
3. One Dave-authorized benign camera action happens.
4. Existing HUD reflects actual resulting state or failure.
5. Restart/offline/retry cannot duplicate the action or leave HUD claiming
   unsubstantiated state.
```

## Waydroid and Android automation

### Planned laptop visual capability

Dave may deliberately show Tigwa selected native desktop applications, including the existing Android camera app running in Waydroid, for inspection and bounded guided interaction.

Prerequisites when the laptop is ready:

```text
logged-in graphical session
cua-driver installed and daemon running in the interactive session
successful `hermes computer-use doctor`
Dave deliberately selects the app/window
```

This is an EA visual/interactive aid, not an unattended monitor or authoritative data source.

### Tasker compatibility

Tasker was previously installed in Waydroid and appeared to work. Complete intended app coverage and reliable inactive-window/background/restart behavior are not yet proven.

Likely useful paths:

```text
time/profile/intent/HTTP actions within Android
camera-app documented intents, plugin, broadcast, or API if available
```

Do not assume host-Linux control, hardware access, boot persistence, screen-off reliability, notification delivery, accessibility/UI automation, or a camera-app API. Waydroid suspension and Android background permissions need a direct fixture.

Test contract is automation-tool-neutral: Tasker is the existing leading candidate, but another Android automation tool may be compared against the same fixture if required.

## Pixel and supporting hardware package

Target family: Pixel, likely a practical older Pixel 7/8-generation unlocked device.

Rationale supplied by Dave:

```text
- reliable Kotlin development-kit target;
- older 7/8 hardware remains practical to acquire;
- same camera architecture needed from newer phones;
- custom-ROM and root capability on unlocked Dave-controlled devices.
```

Custom ROM/root is an enabler, not a routine requirement. The normal photo/measurement/update-packet flow should work without privileged operations.

### Barcode scanner

A supporting hardware package is planned to include a physical barcode scanner.

First fixture:

```text
determine scanner data path: keyboard/HID wedge, Android intent, vendor API,
or other route
→ bind successful scan to existing-SKU selection
→ preserve the unchanged update-packet contract
```

The capture flow remains usable without scanner hardware.

## Turntable / Foldio360 / Ortery

### Foldio360

The currently named root-only exception is a Foldio360 workaround for the intended turntable/camera flow. Root is limited to this documented hardware-integration need unless a later reviewed fixture demonstrates another justified use.

A different turntable remains an explicit alternative. Compare candidates with the same fixture:

```text
reliable control
capture/rotation synchronization
repeatable photo output
recovery after interruption
operator friction
```

The basic capture/update-packet workflow must work even before turntable control exists.

### Ortery USB protocol research

The Ortery is a professional-quality turntable Dave used successfully years ago. Foldio360 displaced it because a rooted Droid 2 made the Foldio workflow faster, not because the Ortery was inadequate.

The research goal is to recover the Ortery positional language. It supports approximately 1,400+ addressable positions.

Current hypothesis — **not yet verified**:

```text
goto <position> left
goto <position> right
stop
```

Possible supporting commands include identity/status/reference and completion/current-position behavior.

The first requirement is a known-good baseline using the original supplied Windows software on a physical Windows PC with the hardware attached. This is a temporary observation fixture, not a production Windows dependency.

Safe observation sequence:

```text
1. Preserve original installer/media, version, documentation, and hashes.
2. Record device enumeration and a known-good software session.
3. Capture USB traffic for small, deliberate safe UI operations.
4. Correlate UI action, USB exchange, device result, and physical state.
5. Test same target in both directions, controlled stop mid-travel,
   already-current target, and returned completion/position state.
6. Separate identity/status from physical-control commands.
7. Do not replay movement/capture commands on live equipment until a bounded
   safety/recovery fixture exists.
```

Preserve the original device representation (position IDs/index/coordinate or other encoding) alongside any friendly capture labels. Do not prematurely reduce it to an assumed “rotate N degrees” abstraction.

A VM can be considered only after original driver/software and reliable USB passthrough are shown. A physical Windows PC is the simplest first baseline.

## Next small fixtures

```text
1. Laptop: inspect existing Waydroid camera app with Dave.
2. Capture current JSON trigger examples and HUD state examples.
3. Prove new-item and existing-SKU packet creation locally.
4. Prove immediate-send and offline store-and-forward delivery state.
5. Test Tasker/current automation candidate with one harmless real trigger.
6. Characterize barcode-scanner input path.
7. Obtain known-good Windows/Ortery software baseline and observe the USB
   positional language.
```
