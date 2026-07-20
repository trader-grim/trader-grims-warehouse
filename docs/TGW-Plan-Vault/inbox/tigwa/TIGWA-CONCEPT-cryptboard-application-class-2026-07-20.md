# Concept: Cryptboard — a potential application class

**State:** `capture-staged` — provisional Dave-originated concept; no branding, release, encryption claim, or implementation authorization
**Working name:** **Cryptboard** (unverified for availability/trademark/conflicts)
**Date:** 2026-07-20

## Core reframing

A clipboard is usually implicit, device-global, short-lived state: many apps may write to it, OS rules constrain observation, and it offers weak destination, receipt, retention, and recovery semantics.

A **Cryptboard** would be an explicit application-level event board:

- a user or an opted-in app intentionally publishes a selected value or structured record;
- the event is encrypted/authenticated for named device(s), recipient(s), or a local workspace;
- recipients, delivery/receipt state, expiry, and history are visible;
- retention and deletion are deliberate policy choices rather than accidental clipboard behavior;
- applications integrate by explicit events, not hidden global clipboard observation.

It is therefore not “an encrypted clipboard.” It is a potentially new interaction/data-transfer class: a governed, observable, recoverable event ledger/board for small handoff payloads.

## Why the TGW proof of concept matters

The camera flow can emit a committed structured event—SKU, location, title/metadata, and photo-group reference—to a named local destination. The wired collection network makes a local-first path practical. The Android pain demonstrates why relying on ambient clipboard history is brittle.

## Product boundary to validate

The first question is whether people understand and want this distinction:

> “Send this selected piece of work to that named place, with privacy, receipt, history, and recovery.”

not:

> “Let an app quietly watch my clipboard.”

## Necessary future checks

- threat model, key custody, revocation, recovery, metadata minimization, and auditability;
- Android/iOS/desktop integration and distribution constraints;
- local-first versus hosted relay boundary;
- latency and workflow friction;
- whether camera capture is the first compelling integration or merely one proof point;
- name/trademark/domain availability before treating `Cryptboard` as a product name.

No cryptographic security representation, code, service, public repository, paid tier, trademark filing, or customer outreach is authorized by this concept record.
