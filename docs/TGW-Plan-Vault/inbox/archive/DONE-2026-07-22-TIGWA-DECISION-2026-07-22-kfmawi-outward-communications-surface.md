# Decision note — KFMAWI as the dedicated outward communications surface

**To:** Claude
**From:** Tigwa, librarian
**Status:** Dave-set direction; design only, no deployment authorization

KFMAWI is not merely a local alarm tablet. Dave designates it as the dedicated **outward communications** device for the Tigwa/Dave loop.

The existing Flutter app already targets Android 10+ and is therefore the intended custom reporting/work surface on KFMAWI. This avoids inventing a separate mobile project or allowing a notification transport to become a second inbox.

## Two deliberate lanes on one dedicated device

1. **Normal operation — Flutter:** custom reports, human inbox/attention list, status context, and explicit review/action surface. It is the durable operator console, through the scoped TGW API/read model.
2. **Outage/independent alert — Tasker:** KFMAWI detects loss of USB charging from the monitored circuit and sends a short power-out/power-restored alarm through the independently powered cellular-router Wi-Fi route. It does not require a1131, tgw-prod, the internal router, Flutter, or a running agent to report the initial outage.

ntfy/Telegram remain delivery/attention transports. They open or point to the Flutter context during normal operation; they do not become the authoritative inbox. Tasker remains the minimal physical-outage annunciator and local Android automation layer.

## Interaction boundary

This is explicitly a Dave-and-Tigwa communication loop. Dave reads and handles communications; Tigwa provides context, reports real exceptions, and preserves the durable state/evidence. The device must support that ordinary human loop rather than automate decisions past Dave or demand ceremonial acknowledgement for routine messages.

## Initial operating constraints

- KFMAWI joins a KFMAWI-only Wi-Fi SSID on the battery-backed cellular router; that path bypasses the internal router for the outage alert.
- Android lock screen and redacted previews are sufficient first-line protection; keep rich work context inside Flutter after normal device/app access.
- The phone/tablet receives no broad broker/database credentials and does not become a message authority.
- First proof is a labelled unplug/replug drill: charger-loss detection, retained cellular route, one delivered outage alert, and one stable restoration alert.

No application build, Tasker installation, router change, permission grant, credential placement, or production trigger is authorized by this note.
