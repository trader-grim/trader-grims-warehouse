import 'dart:io';

// On Linux (bare Wayland/Sway), skip the keyring and use plain files under
// ~/.config/tgw/ so flutter_secure_storage is not required.
// Key → filename mapping keeps the existing ~/.config/tgw/api-key path.
class TgwConfig {
  static String get _dir {
    final home = Platform.environment['HOME'] ?? '';
    return '$home/.config/tgw';
  }

  static String _file(String key) {
    switch (key) {
      case 'bearer_token':
        return 'api-key';
      case 'base_url':
        return 'base-url';
      case 'db_path':
        return 'db-path';
      case 'thumbnail_dir':
        return 'thumbnail-dir';
      default:
        return '$key.cfg';
    }
  }

  static Future<String?> read(String key) async {
    final f = File('$_dir/${_file(key)}');
    if (await f.exists()) return (await f.readAsString()).trim();
    return null;
  }

  static Future<void> write(String key, String value) async {
    final dir = Directory(_dir);
    if (!await dir.exists()) await dir.create(recursive: true);
    await File('$_dir/${_file(key)}').writeAsString(value);
  }
}
