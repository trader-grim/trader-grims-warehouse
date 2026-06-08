import 'dart:convert';
import 'dart:io';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../models/models.dart';

class OfflineDb {
  Database? _db;
  final FlutterSecureStorage _storage = const FlutterSecureStorage();
  String? _dbPath;
  String? _thumbnailDir;
  bool _initialized = false;

  OfflineDb() {
    if (Platform.isLinux || Platform.isWindows) {
      sqfliteFfiInit();
      databaseFactory = databaseFactoryFfi;
    }
  }

  Future<void> ensureInitialized() async {
    if (_initialized) return;
    _dbPath = await _storage.read(key: 'db_path');
    _thumbnailDir = await _storage.read(key: 'thumbnail_dir');
    if (_dbPath != null && await File(_dbPath!).exists()) {
      _db = await openDatabase(_dbPath!, readOnly: true);
    }
    _initialized = true;
  }

  Future<void> setConfig({String? dbPath, String? thumbnailDir}) async {
    if (dbPath != null) {
      _dbPath = dbPath;
      await _storage.write(key: 'db_path', value: dbPath);
      if (_db != null) {
        await _db!.close();
        _db = null;
      }
    }
    if (thumbnailDir != null) {
      _thumbnailDir = thumbnailDir;
      await _storage.write(key: 'thumbnail_dir', value: thumbnailDir);
    }
    _initialized = false;
    await ensureInitialized();
  }

  Future<bool> isOpen() async {
    await ensureInitialized();
    return _db != null;
  }

  Future<List<ItemSummary>> getItems({
    String? search,
    String? location,
    String? statusFilter,
    int limit = 200,
    int offset = 0,
  }) async {
    if (!await isOpen()) return [];

    String where = '1=1';
    List<dynamic> args = [];

    if (search != null && search.isNotEmpty) {
      where += ' AND (title LIKE ? OR sku LIKE ?)';
      args.addAll(['%$search%', '%$search%']);
    }
    if (location != null && location.isNotEmpty) {
      where += ' AND location = ?';
      args.add(location);
    }
    if (statusFilter != null && statusFilter.isNotEmpty) {
      where += ' AND status = ?';
      args.add(statusFilter);
    }

    final results = await _db!.query(
      'catalog',
      columns: ['sku', 'title', 'location', 'status', 'price', 'qty', 'image'],
      where: where,
      whereArgs: args,
      orderBy: 'sku DESC',
      limit: limit,
      offset: offset,
    );

    return results.map((r) => ItemSummary.fromJson(r)).toList();
  }

  Future<ItemDetail?> getItem(String sku) async {
    if (!await isOpen()) return null;

    final results = await _db!.query(
      'catalog',
      columns: ['data'],
      where: 'sku = ?',
      whereArgs: [sku],
    );

    if (results.isEmpty) return null;

    final dataJson = json.decode(results.first['data'] as String);
    return ItemDetail.fromJson({
      'ok': true,
      'item': dataJson,
      'images': [], // Offline images handled by local file path
      'videos': [],
      'queue_jobs': [],
    });
  }

  Future<List<String>> getLocations() async {
    if (!await isOpen()) return [];
    final results = await _db!.rawQuery('SELECT DISTINCT location FROM catalog ORDER BY location');
    return results.map((r) => r['location'] as String).toList();
  }
  
  String? getLocalThumbnailPath(String sku) {
    if (_thumbnailDir == null || _thumbnailDir!.isEmpty) return null;
    return '$_thumbnailDir/$sku.jpg';
  }

  String? get thumbnailDir => _thumbnailDir;
}
