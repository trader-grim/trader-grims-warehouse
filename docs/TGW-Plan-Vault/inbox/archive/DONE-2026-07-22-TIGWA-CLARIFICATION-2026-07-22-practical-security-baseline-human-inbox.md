# Clarification — practical security baseline for the human inbox/ntfy lane

**To:** Claude
**From:** Tigwa, librarian
**Status:** Dave-set design constraint; no deployment authorization
**Applies to:** the ntfy/Flutter reconciliation addendum and any future human-inbox packet.

Dave's direction is explicit: security must be proportionate to a one-person operator system. The goal is not theoretical maximal isolation at the cost of an unusable phone/workflow. Start with a locking screen saver and a reasonable retention policy; add controls only when a concrete threat, evidence class, or operational failure requires them.

## Minimum useful baseline

1. Keep the notification service private to the Tailnet; do not expose an unauthenticated public endpoint.
2. The Android device uses its normal lock screen. Notifications on the lock screen are redacted to a title/severity/count; opening the Flutter work item requires the device/app's normal access control.
3. Treat notification payloads as a short-lived attention envelope, never a source of secret material, raw evidence, credentials, or full customer/listing data.
4. Use distinct, revocable publisher and phone/client credentials. Device loss means revoke/replace that one identity, not rotate unrelated infrastructure.
5. Configure retention deliberately by message class, with the value chosen by Dave and recorded in configuration. The ntfy retention window is convenience/retry only; authoritative work/evidence retention remains in the approved mailbox/state system.
6. Keep explicit acknowledgements, snoozes, and completions in the existing authoritative transition/log path. Dismissing or losing a phone notification must have no hidden state effect.
7. Start with one synthetic low-sensitivity event and inspect actual phone usability before adding actions, richer payloads, or more hardening.

## Non-goals for the first lane

- no public service, no internet-facing webhook receiver, no custom cryptographic protocol;
- no ambient clipboard monitoring, device surveillance, or broad Android permissions;
- no direct phone access to broad NATS subjects or production databases;
- no attempt to make notification transport itself an archival system;
- no security theater that makes normal acknowledgement or operator recovery impractical.

Future controls should earn their complexity from a demonstrated need: an actual exposure, a new evidence/retention class, a new device/user boundary, or a failed recovery/drill—not from abstract maximal-security comparisons.
