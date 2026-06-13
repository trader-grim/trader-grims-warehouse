You are working in the Trader Grim's Warehouse (TGW) repo at
`/opt/TGW/src/trader-grims-warehouse`. Read CLAUDE.md and CONVENTIONS.md before
making any changes; do not deviate from them.

Task: todo #150 — PP-PORTABLE-CATALOG-001 P2 offline data layer (Flutter/Dart)

Full spec: docs/TGW-Plan-Vault/reference/PP-PORTABLE-CATALOG-001-P2-prototype-brief.md
Read that document first — it contains the complete design, endpoint contracts, and
acceptance criteria. This message summarises the key changes; the brief is authoritative.

---

## What to build

### 1. Add dependencies to apps/tgw_app/pubspec.yaml

Add under `dependencies:`:
- connectivity_plus: ^6.0.3
- dio_smart_retry: ^6.0.0
- workmanager: ^0.5.2

Do NOT add `sqlite3` — sqflite_common_ffi already covers Linux.

### 2. Snapshot-to-sandbox in apps/tgw_app/lib/db/offline_db.dart

Change the db open strategy:
- Add `_sandboxPath` computed from `path_provider`'s `getApplicationSupportDirectory()`
  → `<support>/tgw_sandbox/catalog.db`
- Add `Future<void> initSnapshot(ApiClient apiClient, bool isOnline)`:
  - If online AND sandbox is stale (>24h or absent): GET /api/catalog/snapshot → save to
    sandbox path → open read-write
  - If offline AND sandbox absent: File.copy(user-set synced path → sandbox) → open read-write
  - If sandbox exists and fresh (<24h): open existing sandbox read-write
- Change `openDatabase` call to open `_sandboxPath` with `readOnly: false`
- Keep the user-set `db_path` SecureStorage key as the fallback offline source path only

### 3. Outbox table in apps/tgw_app/lib/db/offline_db.dart

At database open time, create the outbox table if not exists:

```sql
CREATE TABLE IF NOT EXISTS outbox (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  created  TEXT NOT NULL,
  op       TEXT NOT NULL,
  sku      TEXT NOT NULL,
  payload  TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
)
```

Add methods:
- `Future<void> insertOutbox(String op, String sku, String payload)`
- `Future<List<Map<String, dynamic>>> getPendingOutbox()`
- `Future<void> deleteOutboxRow(int id)`
- `Future<void> updateItemLocal(String sku, Map<String, dynamic> fields)` — merges fields
  into the existing JSON blob in the `data` column of the `catalog` table

### 4. Connectivity flush in apps/tgw_app/lib/providers/providers.dart

Replace the one-shot `ConnectionStatusNotifier` with a periodic poller:
- Poll every 15 seconds via `Timer.periodic`
- On transition offline→online: flush the outbox
  - For each row: if `op=='patch'` call `apiClient.patchItem(sku, fields)`; if `op=='action'`
    call `apiClient.performAction(sku, payload)`
  - On success: `offlineDb.deleteOutboxRow(id)`; on failure: increment attempts (leave row)
  - After flush: call `offlineDb.initSnapshot(apiClient, true)` to refresh the local copy
- Add `outboxCountProvider` (FutureProvider<int>) querying `getPendingOutbox().length`
- Add `workmanager` registration in main.dart behind `if (Platform.isAndroid)` guard —
  stub is acceptable on Linux

### 5. Offline routing in apps/tgw_app/lib/repository/repository.dart

- `patchItem(sku, fields)`: if offline → `offlineDb.insertOutbox('patch', sku, json.encode(fields))`
  then `offlineDb.updateItemLocal(sku, fields)`; return true
- `performAction(sku, action)`: if offline → `offlineDb.insertOutbox('action', sku, action)`;
  return 'offline-queued'

---

## Files to modify

- apps/tgw_app/pubspec.yaml
- apps/tgw_app/lib/db/offline_db.dart
- apps/tgw_app/lib/providers/providers.dart
- apps/tgw_app/lib/repository/repository.dart
- apps/tgw_app/lib/main.dart          (workmanager stub + snapshot init call)

Do NOT touch: src/tgw/, config, secrets, anything outside apps/tgw_app/.

---

## Acceptance

`flutter analyze` passes with no errors (warnings acceptable).
The `flutter run -d linux` command should launch without crash after todo #151 wires the UI.
If a requirement is impossible as specified, stop and explain rather than improvising.

---

## Run command (for reference)

Uses Gemini 3.1 Flash-Lite via OpenRouter — native Dart/Flutter knowledge, cheap.
OPENROUTER_API_KEY loaded automatically from /home/tgw/.env via .aider.conf.yml.

```bash
cd /opt/TGW/src/trader-grims-warehouse
git checkout -b feature/portable-catalog-p2
aider --model openrouter/google/gemini-3.1-flash-lite \
  --no-auto-lint --test-cmd "flutter analyze --project-path apps/tgw_app" \
  apps/tgw_app/pubspec.yaml \
  apps/tgw_app/lib/db/offline_db.dart \
  apps/tgw_app/lib/repository/repository.dart \
  apps/tgw_app/lib/providers/providers.dart \
  apps/tgw_app/lib/main.dart \
  --message-file aider/spec-150-portable-catalog-p2-data-layer.md
```
