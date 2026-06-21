import 'dart:io';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import '../api/api_client.dart';
import '../db/offline_db.dart';

enum SyncResult { fresh, cached, failed }

class CatalogSyncService {
  final ApiClient _api;
  final OfflineDb _offlineDb;

  CatalogSyncService(this._api, this._offlineDb);

  // Private copy path — never open the Syncthing-synced file directly.
  Future<String> get _privatePath async {
    final dir = await getApplicationSupportDirectory();
    return p.join(dir.path, 'tgwcatalog_private.db');
  }

  Future<SyncResult> sync() async {
    final dest = await _privatePath;

    // Try to download a fresh snapshot from the server.
    final tmpPath = '$dest.tmp';
    final result = await _api.downloadSnapshot(tmpPath);

    if (result.ok) {
      // Atomically replace the private copy.
      await File(tmpPath).rename(dest);
      await _offlineDb.setConfig(dbPath: dest);
      return SyncResult.fresh;
    }

    // Download failed — fall back to existing private copy if present.
    if (await File(dest).exists()) {
      await _offlineDb.setConfig(dbPath: dest);
      return SyncResult.cached;
    }

    return SyncResult.failed;
  }

  Future<bool> hasLocalCopy() async {
    final dest = await _privatePath;
    return File(dest).exists();
  }
}
