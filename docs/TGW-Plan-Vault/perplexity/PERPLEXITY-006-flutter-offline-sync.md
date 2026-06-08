# PERPLEXITY-006 — Flutter Offline-First + Syncthing-Synced SQLite

**Date prepared:** 2026-06-08
**Prepared by:** Claude (Opus 4.8), session 19 delegation pass
**Priority:** MEDIUM-HIGH (de-risks the Flutter build — GEMINI-TASK-003)
**How to run:** Paste the prompt below into Perplexity (with web/citations on). Save the result
as `PERPLEXITY-006-result.md` into `docs/TGW-Plan-Vault/inbox/` for PM-intake.
**What it unblocks:** PP-EDITOR-001 Flutter app (Phase B–E), PP-PORTABLE-CATALOG-001 sync design.

---

## Why this brief

We're building a Flutter app (Linux desktop + Android tablet) that reads a Syncthing-synced
SQLite catalog (`tgwcatalog.db`, ~180 MB, 83K rows) offline and writes through a FastAPI service
(`tgw-http`, Bearer auth) when online. Before we commit the architecture we want current (2025–
2026), cited best-practice on the tricky parts. We need footnoted sources because the subscription
expires ~2026-12 and we want a durable reference.

## Prompt (paste into Perplexity)

> I'm building a Flutter app (targets: Linux desktop + Android 10+) that is **offline-first** over a
> ~180 MB SQLite database (~83,000 rows) which is **synced to the device by Syncthing**, and which
> writes through a remote FastAPI HTTP service when online. Give me current (2025–2026), cited best
> practices on the following, with footnoted sources:
>
> 1. **Reading a Syncthing-synced SQLite file from sqflite safely.** Syncthing may replace the DB
>    file underneath the app at any time. What are the failure modes (WAL/SHM files, file-replace
>    vs in-place write, locking, ".sync-conflict" files) and the recommended pattern — copy-to-app-
>    sandbox before open? read-only open? watch for changes and reopen? On Android specifically,
>    can sqflite open a DB in a Syncthing-managed external folder, or must it be copied into app
>    storage first? How does this differ on Linux desktop (sqflite_common_ffi)?
> 2. **Best library choices in 2025–2026** for: (a) SQLite access on both Linux desktop and Android
>    from one codebase (sqflite vs drift vs sqlite3 ffi), (b) HTTP client with retry/offline queue
>    (dio + which interceptors/plugins), (c) secure token storage on Android + Linux
>    (flutter_secure_storage state of support on Linux desktop in 2026). Note maintenance status.
> 3. **Offline write queue pattern.** The app must let the operator queue edits while offline and
>    flush them to the FastAPI service when connectivity returns, idempotently. What's the current
>    recommended approach in Flutter (outbox table + connectivity listener + background flush)?
>    Any well-maintained packages, or roll-our-own?
> 4. **Flutter Linux desktop packaging in 2026** — current state of distributing a Flutter Linux
>    app (bundle, .deb, flatpak, AppImage), and any gotchas with sqlite ffi + secure storage on
>    Linux. Also: building/signing for Android sideload (no Play Store).
> 5. **Connectivity detection** — reliable online/offline detection that distinguishes "network up
>    but my API host unreachable" from "no network." Recommended approach (connectivity_plus +
>    an explicit health ping).
> 6. **Conflict avoidance** — since the DB is read-only on the device and all writes go through the
>    server, what's the cleanest way to avoid Syncthing sync-conflicts on the SQLite file (e.g. the
>    device never writes the synced DB; writes go server-side and propagate back)? Confirm this is
>    the right model and note any edge cases.
>
> For each area give a concrete recommendation, the trade-offs, and cite sources (official docs,
> pub.dev package pages with maintenance signals, recent posts). Flag anything that changed in the
> last 12 months.

## What to capture in the result
- A recommended dependency list with versions + maintenance status.
- The recommended **DB-read strategy** (copy-to-sandbox vs direct read) for Android and Linux.
- The recommended **offline write-queue** pattern (and whether a package exists or we build it).
- Packaging recommendation for Linux + Android sideload.
- Any "do not do this" warnings specific to Syncthing + SQLite.
- Footnoted sources throughout.
