# PP-PORTABLE-CATALOG-001 Phase 2 — Offline-First Flutter Prototype Brief

**For: AGY (Antigravity CLI)** | **Todos: #150 (data layer) + #151 (wire into screens)**  
**Repo:** `/opt/TGW/src/trader-grims-warehouse`  
**Flutter app:** `apps/tgw_app/`

---

## Goal

Make `apps/tgw_app/` a runnable **offline-first prototype** on `flutter run -d linux`.  
Dave needs to see the offline-edit → reconnect → flush loop to start conceiving the interfaces.  
"Partially working" is the target — not a hardened engine, not UI polish.

The prototype must demonstrate:
1. Browse items from a local db copy while the API is unreachable
2. Edit an item while offline — the change persists locally
3. Come back online — the queued edit flushes to the server

---

## What already exists (do not rewrite)

### Python backend — complete, no changes needed

| Endpoint | What it does |
|----------|-------------|
| `GET /api/catalog/snapshot` | Streams an atomic `sqlite3.backup()` copy of `tgwcatalog.db` as `application/octet-stream` |
| `GET /api/queue/status` | Connectivity probe — returns 200 if tgw-http is up; already used by `checkConnection()` |
| `PATCH /api/items/{sku}` | Multi-field update: `{"fields": {"title": "...", ...}}` |
| `POST /api/items/{sku}/action` | Enqueues a pipeline action: `{"action": "ai_identify"}` |
| `GET /api/items` / `GET /api/items/{sku}` | Browse + detail reads |

All endpoints require `Authorization: Bearer <token>` header.

### Flutter code to reuse (don't rewrite, build on top)

**`lib/api/api_client.dart`** — `ApiClient` class:
- `Dio` HTTP client, bearer token from `FlutterSecureStorage`, base URL configurable
- `checkConnection()` pings `GET /api/queue/status` → bool
- `patchItem(sku, fields)`, `performAction(sku, action)` — the mutation methods
- `ensureInitialized()` reads stored config from `FlutterSecureStorage`

**`lib/db/offline_db.dart`** — `OfflineDb` class:
- `sqflite` + `sqflite_common_ffi` (Linux FFI init in constructor)
- Currently opens the synced file **read-only** at a user-set path — **this needs to change** (see data layer below)
- Has `getItems(...)`, `getItem(sku)`, `getLocations()` — keep these, they query the `catalog` table

**`lib/repository/repository.dart`** — `TgwRepository`:
- Reads: route to API when `connectionStatusProvider == online`, fall back to `offlineDb`
- Writes: `patchItem` and `performAction` go straight to API, fail silently offline — **this needs the outbox**

**`lib/providers/providers.dart`** — Riverpod providers:
- `connectionStatusProvider` (`StateNotifierProvider<ConnectionStatusNotifier, ConnectionStatus>`)
- `ConnectionStatusNotifier` does one-shot check on construction — **extend to periodic poll + flush trigger**
- `repositoryProvider`, `apiClientProvider`, `offlineDbProvider`

**`pubspec.yaml`** — already has: `dio`, `sqflite`, `sqflite_common_ffi`, `flutter_secure_storage`, `path_provider`, `path`, `flutter_riverpod`

---

## What to build (todo #150 — data layer)

### Step 1: Add missing dependencies to `pubspec.yaml`

```yaml
connectivity_plus: ^6.0.3    # OS-level network state; triggers flush on reconnect
dio_smart_retry: ^6.0.0      # Automatic retry on transient network errors
workmanager: ^0.5.2          # Android background flush; stub/no-op acceptable on Linux
```

Note: do NOT add `sqlite3` package — `sqflite_common_ffi` already provides the SQLite engine
on Linux. Only add `sqlite3` if you hit a specific gap the existing packages cannot cover.

### Step 2: Snapshot-to-sandbox (replace in-place open)

The synced `tgwcatalog.db` must never be opened directly — Syncthing may be writing to it.
The app must copy it to a private sandbox first.

**Location for the private copy:** `path_provider`'s `getApplicationSupportDirectory()` →
`<support>/tgw_sandbox/catalog.db`

**When online** (preferred): pull a fresh atomic copy from `GET /api/catalog/snapshot`, save
to the sandbox path, open it read-write.

**When offline**: copy the user-configured synced file path to the sandbox with `File.copy()`,
open the copy read-write.

**On subsequent launches**: if the sandbox copy exists and is < 24h old (check `FileStat.modified`),
skip the download/copy and open the existing sandbox copy.

Modify `OfflineDb`:
- Add `_sandboxPath` computed from `getApplicationSupportDirectory()`
- Add `Future<void> initSnapshot(ApiClient apiClient, ConnectionStatus status)` that does
  the copy/download logic above
- Change `openDatabase` call: open `_sandboxPath` with `readOnly: false` (it's our private copy)
- Remove the user-set `db_path` SecureStorage key for the source path — keep it only as the
  fallback synced file path for offline copy

### Step 3: Outbox table

Add an `outbox` table to the sandbox db at open time (created if not exists):

```sql
CREATE TABLE IF NOT EXISTS outbox (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  created  TEXT NOT NULL,         -- ISO8601 UTC
  op       TEXT NOT NULL,         -- 'patch' | 'action'
  sku      TEXT NOT NULL,
  payload  TEXT NOT NULL,         -- JSON-encoded fields map or action name
  attempts INTEGER NOT NULL DEFAULT 0
)
```

In `TgwRepository`:
- `patchItem(sku, fields)`: if **online** → API call as now; if **offline** → INSERT into outbox
  with `op='patch'`, `payload=json.encode(fields)`. Return `true` (optimistic).
- `performAction(sku, action)`: if **online** → API call as now; if **offline** → INSERT into outbox
  with `op='action'`, `payload=action`. Return a fake job id string.

Add `OfflineDb.getPendingOutbox()` → `List<Map>` (SELECT * FROM outbox ORDER BY id)  
Add `OfflineDb.deleteOutboxRow(int id)` — called after successful flush

### Step 4: Connectivity watch + flush

Replace the one-shot `ConnectionStatusNotifier` with a periodic poller:

```dart
// In ConnectionStatusNotifier constructor:
Timer.periodic(const Duration(seconds: 15), (_) => _checkAndFlush());

Future<void> _checkAndFlush() async {
  final wasOffline = state == ConnectionStatus.offline;
  final isOnline = await _apiClient.checkConnection();
  state = isOnline ? ConnectionStatus.online : ConnectionStatus.offline;
  if (isOnline && wasOffline) {
    // transitioned online — flush
    await _flushOutbox();
  }
}
```

`_flushOutbox()` reads all outbox rows, calls the matching API method for each, deletes
successful rows. On any failure: increment `attempts`, leave the row for next flush (do not
surface errors to the user in the prototype — just log to console).

After flushing: trigger a snapshot refresh — call `offlineDb.initSnapshot(apiClient, online)`
to pull a fresh copy from `/api/catalog/snapshot`. Invalidate the `itemListProvider` so the
browse screen refreshes.

**`workmanager` on Android** (not Linux): register a periodic background task in `main.dart`
that calls `_flushOutbox()`. On Linux, the periodic `Timer` in the notifier is sufficient.
For the Linux prototype, stub `workmanager` registration behind `if (Platform.isAndroid)`.

---

## What to build (todo #151 — wire into screens)

### Screen changes

The goal is that the offline-first behavior is **visible** — a user can see:
- A status chip in the app bar: `● Online` (green) / `● Offline` (amber)
- A pending-edits badge: `2 pending` when outbox rows exist
- On the Edit screen: a "Saved offline" snackbar when a patch queues to outbox
- On reconnect: a "Syncing..." then "Synced" snackbar

These are minimal UI indicators — not a full status dashboard.

### Specific wiring

1. **App bar / home screen**: watch `connectionStatusProvider` + add a new
   `outboxCountProvider` (StreamProvider that queries outbox row count from `OfflineDb`).
   Display the status chip + pending badge.

2. **Edit screen** (`features/item/edit_item_screen.dart`): after calling
   `repository.patchItem(sku, fields)`, check if the result was queued (offline) and show
   the appropriate snackbar. The edit should also apply to the local sandbox db so the item
   detail screen reflects it immediately — add `OfflineDb.updateItemLocal(sku, fields)` that
   does a raw `UPDATE catalog SET data = ... WHERE sku = ?` merging the changed fields into
   the existing JSON blob.

3. **Initialization**: in `main.dart` or `app.dart`, after Riverpod scope setup, call
   `offlineDb.initSnapshot(apiClient, initialStatus)` before showing the first screen.
   Show a loading indicator during snapshot init (it may take a second on first launch
   if downloading from `/api/catalog/snapshot`).

4. **Flush indicator**: in `ConnectionStatusNotifier._flushOutbox()`, set a transient
   `isFlushing` bool on the notifier and expose it; the UI shows "Syncing..." while true.

---

## Acceptance

- `flutter run -d linux` starts without crash on the dev machine
- Settings screen: enter tgw-http base URL + bearer token (already works — don't break)
- Browse screen loads items from the sandbox db when offline
- Edit an item while offline → Save → "Saved offline" snackbar appears, outbox badge increments
- Restore connectivity (or set API base URL back) → within 15s the flush runs → "Synced" appears
- `flutter analyze` clean (no errors; warnings OK for prototype)
- Drop a screenshot of the offline badge + edit + synced flow into `docs/TGW-Plan-Vault/inbox/`
  as `PP-PORTABLE-CATALOG-001-P2-screenshot.md` (or png)

---

## Hard constraints

- **Do NOT touch**: `src/tgw/` (Python), config files, secrets, anything under `src/tgw/ebay/`
- **Do NOT add eBay scopes or API keys**
- **Branch**: create `feature/portable-catalog-p2` — do not merge
- **Commits**: commit to the task branch only; Dave reviews + merges
- Reuse `{ok: bool, ...}` API response shape as-is; do not remap field names
- If a requirement is impossible as specified, stop and explain rather than improvising

---

## File map (files you may modify)

```
apps/tgw_app/pubspec.yaml                        add deps
apps/tgw_app/lib/db/offline_db.dart              snapshot-to-sandbox, outbox CRUD
apps/tgw_app/lib/repository/repository.dart      outbox routing for patchItem/performAction
apps/tgw_app/lib/providers/providers.dart        periodic connectivity + outboxCountProvider
apps/tgw_app/lib/main.dart                       snapshot init, workmanager stub
apps/tgw_app/lib/app.dart                        pass through; may need minor wiring
apps/tgw_app/lib/features/item/edit_item_screen.dart   snackbar + offline save feedback
apps/tgw_app/lib/features/*/                     status chip + pending badge in app bar
```

New files you may create (if needed):
```
apps/tgw_app/lib/db/sync_service.dart            optional: isolate flush logic
```

---

## Build environment notes

Linux desktop build only for the prototype.

```bash
# Build deps (already installed, verify with dpkg -l):
libsecret-1-dev libjsoncpp-dev libsecret-1-0

# Run:
cd /opt/TGW/src/trader-grims-warehouse/apps/tgw_app
flutter run -d linux

# Analyze:
flutter analyze
```

tgw-http runs on the dev machine at `http://127.0.0.1:7373`. Bearer token is in
`/opt/TGW/secrets/tgw-api-key.json` → `{"api_key": "..."}`. The user sets these in
the app's Settings screen.
