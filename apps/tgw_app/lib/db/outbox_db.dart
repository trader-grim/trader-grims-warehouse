import 'dart:convert';
import 'dart:io';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class OutboxMutation {
  final int id;
  final String sku;
  final Map<String, dynamic> fields;
  final String queuedAt;
  final int attempts;

  OutboxMutation({
    required this.id,
    required this.sku,
    required this.fields,
    required this.queuedAt,
    required this.attempts,
  });

  factory OutboxMutation.fromRow(Map<String, dynamic> row) => OutboxMutation(
        id: row['id'] as int,
        sku: row['sku'] as String,
        fields: json.decode(row['fields'] as String) as Map<String, dynamic>,
        queuedAt: row['queued_at'] as String,
        attempts: row['attempts'] as int,
      );
}

class OutboxDb {
  Database? _db;

  OutboxDb() {
    if (Platform.isLinux || Platform.isWindows) {
      sqfliteFfiInit();
      databaseFactory = databaseFactoryFfi;
    }
  }

  Future<Database> _open() async {
    if (_db != null) return _db!;
    final dir = await getApplicationSupportDirectory();
    final path = p.join(dir.path, 'tgw_outbox.db');
    _db = await openDatabase(
      path,
      version: 1,
      onCreate: (db, _) async {
        await db.execute('''
          CREATE TABLE pending_mutations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            sku       TEXT NOT NULL,
            fields    TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            attempts  INTEGER NOT NULL DEFAULT 0
          )
        ''');
      },
    );
    return _db!;
  }

  Future<void> enqueue(String sku, Map<String, dynamic> fields) async {
    final db = await _open();
    await db.insert('pending_mutations', {
      'sku': sku,
      'fields': json.encode(fields),
      'queued_at': DateTime.now().toUtc().toIso8601String(),
      'attempts': 0,
    });
  }

  Future<List<OutboxMutation>> pending() async {
    final db = await _open();
    final rows = await db.query('pending_mutations', orderBy: 'id ASC');
    return rows.map(OutboxMutation.fromRow).toList();
  }

  Future<void> markAttempt(int id) async {
    final db = await _open();
    await db.rawUpdate(
      'UPDATE pending_mutations SET attempts = attempts + 1 WHERE id = ?',
      [id],
    );
  }

  Future<void> remove(int id) async {
    final db = await _open();
    await db.delete('pending_mutations', where: 'id = ?', whereArgs: [id]);
  }

  Future<int> pendingCount() async {
    final db = await _open();
    final result = await db.rawQuery('SELECT COUNT(*) AS n FROM pending_mutations');
    return result.first['n'] as int;
  }
}
