# Addendum — reconcile ntfy with the Flutter operator app

**To:** Claude
**From:** Tigwa, librarian
**Status:** architecture clarification / no build or deployment authorization
**Supersedes nothing:** additive to `TIGWA-NOTE-2026-07-22-ntfy-human-inbox-connection.md`

## Why this addendum exists

Dave correctly flagged that ntfy must be reconciled with the existing Flutter app rather than becoming a parallel human inbox. The Flutter client is already the settled primary operator surface: Linux desktop plus Android tablet, mostly keyboard-free. The original scaffold explicitly placed push notifications out of its Phase B/C scope; that is a sequencing boundary, not a decision to omit them permanently.

Verified current surface:

- `apps/tgw_app/` exists and has Home, Browse, Review, Item, and Settings routes.
- It already models online/offline state and talks to `tgw-http` through a repository/API boundary.
- Its current offline mode is a local catalog copy for browsing; that must not be mistaken for a reliable mailbox mirror.

Relevant authority anchors:

- `GEMINI-TASK-003-flutter-app-scaffold.md` lines 18–40 and 62–67: Android tablet is the primary operator surface; push notifications were explicitly a later phase.
- `PP-RUNNERCOMMS-001`, canonical Master Plan lines 608–715: JetStream is the shared mailbox transport; PostgreSQL remains work-state authority; human-readable files are export/record only; stale/unavailable state must be visible.
- RETARGET Track R2.3: KDE Connect / ntfy is the phone-alert route.

## Settled division of responsibility

```text
JetStream + PostgreSQL + evidence/remote-log services
        ↑ authoritative message/work state
scoped TGW operator API / notification adapter
        ├─ Flutter app: human inbox, thread/detail, review, deliberate actions
        ├─ ntfy: wake-up/attention notification + deep link
        ├─ Tasker: local Android presentation/automation only
        └─ KDE Connect: explicit bootstrap/manual transport only
```

### Flutter is the human inbox

The Flutter app should own the durable human interaction:

- inbox/needs-attention list, per-thread timeline, provenance and revision state;
- message detail with source packet/contract/evidence links;
- clear `new / delivered / read / acknowledged / snoozed / completed / stale / unavailable` presentation;
- explicit human actions, confirmation where needed, and a visible action/result receipt;
- startup summary: due reminders, blocked questions, delivery/integrity exceptions, and relevant red health — not an unbounded feed.

The app fetches a scoped read model from the operator API. It does **not** receive broad NATS credentials, query raw mailbox subjects, or use a local cache as proof that a current message was delivered.

### ntfy is the attention layer

ntfy does not create a second inbox or durable action authority. It carries a minimal envelope:

- stable `message_id`, thread/parent correlation, severity, title, short redacted summary, expiry;
- an authenticated/deep link into the precise Flutter route;
- optionally a narrow action that opens the app or requests a signed, idempotent action endpoint.

A notification receipt/arrival is distinct from app open/read/acknowledgement. A notification can be dropped, delayed, or dismissed without changing mailbox state. On launch, Flutter reads the authoritative current view, so the phone sees the actual state rather than trusting the push payload.

### Tasker and KDE Connect remain useful but subordinate

Tasker may customize sound, notification behavior, or open the same Flutter deep link. It must not become the record of completion. KDE Connect's known-good explicit clipboard dispatch may carry a selected bootstrap/deep-link payload, but never ambient clipboard synchronization, secrets, or event-log semantics.

## Offline behavior

The catalog's offline browse contract does not automatically extend to the inbox.

- Flutter may retain a clearly labelled cached, read-only inbox view for convenience.
- If its authoritative API is unreachable, it must show `STALE`/`UNAVAILABLE`, last verified time, and disable actions that require current authority.
- The app must not infer an acknowledgement/completion from a locally cached notification or enqueue an untraceable mailbox mutation.

## Build sequencing — proposed, not authorized

1. Finish/packet the existing JetStream mailbox substrate and its delivery/read/acknowledgement evidence contract.
2. Define the narrow operator read/action API and deep-link identity contract. Include actor/device scope, message/revision/parent IDs, content hashes, stale handling, and idempotency.
3. Add a small Flutter Inbox/Attention shell against a synthetic/read-only API fixture; prove offline/stale rendering before real actions.
4. Add ntfy as the notification adapter, with one synthetic due/red event opening the exact Flutter item.
5. Add bounded acknowledgement/snooze actions only after the state-transition and remote-log receipt path is demonstrated.
6. Decide whether any Tasker customization earns retention after real phone-use evidence.

This leaves the existing Flutter app as the unified human surface and makes ntfy valuable precisely because it gets Dave to the right Flutter thread at the right time.
