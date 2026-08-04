# Ntfy connection: canonical R2 alert route → planned human inbox attention bridge

**To:** Claude
**From:** Tigwa, librarian
**Status:** design connection / no deployment authorization
**Linked canonical anchor:** `docs/TGW-Plan-Vault/plan/RETARGET-2026-07-02.md`, Track R2, lines 135–142; especially R2.3.

## The pre-existing connection

The canonical RETARGET plan already names the relevant direction:

> **R2.3 Push on red:** digest failure lines go through KDE Connect / ntfy to Dave's phone (mechanism = whatever's already paired, no new infra).

That predates this discussion. Dave independently recalled that Claude had also suggested ntfy. The current conclusion is therefore a convergence with canonical planning, not a new product direction.

## Current operator evidence

- Join's clipboard route has reliability issues.
- KDE Connect's direct route is known to work: on a1131 it is the `db` desktop session's D-Bus/KDE Connect pairing, not Tigwa's own service session. It can dispatch an explicit selected clipboard payload to Dave's paired Android device across the established private route.
- That makes KDE Connect useful immediately for explicit one-off/bootstrapping delivery. It is not a durable human inbox: clipboard transport has no reliable message identity, acknowledgment, threading, retry state, or evidence trail.
- Dave needs a better human-facing inbox and wants startup/due reminders surfaced on Android. This surface is already planned; it must not invent a second work authority.

## Why ntfy is the likely attention-layer candidate

Current official ntfy documentation supports self-hosting, Android clients, HTTP publishing, WebSocket/JSON/SSE subscription, cache/replay, priority channels, deep links, Tasker integration, and notification action buttons. This supplies the missing phone-facing socket/push layer that KDE Connect clipboard deliberately does not provide.

Gotify is a credible simpler WebSocket push alternative, but ntfy better matches the needed operator interaction surface: action buttons, Android/Tasker integration, scheduled/priority delivery, and a first-class HTTP/WebSocket protocol.

## Proposed responsibility boundary

```text
PostgreSQL work-state authority + NATS JetStream durable mailbox
  -> scoped Tigwa Lite notification bridge (a1131 prototype)
  -> ntfy over Tailscale
  -> Android ntfy UI and/or Tasker local automation
  -> signed bounded acknowledge/snooze/complete callback
  -> PostgreSQL transition + remote-log receipt
```

- **PostgreSQL + JetStream:** canonical work state, durable delivery/outbox, mailbox/trace evidence. Never replace either with ntfy.
- **Tigwa Lite:** bounded notifier/adapter only. It reads an approved due/red event and publishes an envelope; it does not decide policy or become the catalog authority.
- **ntfy:** attention and live socket-bus transport, not the authoritative task database.
- **Tasker:** local Android presentation/automation. It must receive only explicit selected payloads and send only scoped signed acknowledgement actions.
- **KDE Connect:** retained as a working private manual/bootstrap transport (e.g., an exact deep link or selected text); not ambient clipboard mirroring and not the event ledger.

## Security and integrity requirements before a spike

1. Keep the server private to the Tailnet; no public unauthenticated topic names treated as security.
2. Use distinct authenticated publisher/subscriber identities and narrowly scoped topics. Do not place secrets, credentials, raw evidence bundles, or unrestricted commands in a notification.
3. Every alert includes a stable event/message ID and references the originating state/contract hash. Notification arrival is not acknowledgement.
4. Action callbacks carry short-lived, single-purpose signed capabilities, are idempotent, and effect an explicit PostgreSQL state transition before recording a remote-log receipt.
5. Preserve the source event, rendered notification revision, delivery attempt/result, and human acknowledgement separately. Silence is not approval.
6. Account for Android background behavior: a self-hosted ntfy server uses the Android client's persistent/foreground delivery connection for prompt delivery rather than relying on generic cloud push.
7. Do not change flake, deploy a server, alter gateway routes, accept phone permissions, or migrate live notification paths under this note.

## Smallest useful next packet (when Dave chooses it)

A read-only, no-production-mutation a1131 spike:

- confirm the a1131 host trust/identity and current Tailscale reachability;
- inspect whether a private ntfy instance can be made available without touching the production authority or flake without its owner's approval;
- use one non-sensitive synthetic event with a generated ID;
- verify Android receipt and an explicit manual acknowledgement round trip;
- retain hashes/remote-log receipts and report latency, Android behavior, and any missing acknowledgement semantics;
- decide afterward whether the adapter belongs in the existing R2.3 alert route, the planned human inbox, both, or neither.

No production install or cutover is authorized by this proposal.
