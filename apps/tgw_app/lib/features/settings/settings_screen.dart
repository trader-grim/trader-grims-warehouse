import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/providers.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  final _baseUrlController = TextEditingController();
  final _tokenController = TextEditingController();
  final _dbPathController = TextEditingController();
  final _thumbDirController = TextEditingController();
  final _storage = const FlutterSecureStorage();

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    _baseUrlController.text = await _storage.read(key: 'base_url') ?? 'http://127.0.0.1:7373';
    _tokenController.text = await _storage.read(key: 'bearer_token') ?? '';
    _dbPathController.text = await _storage.read(key: 'db_path') ?? '';
    _thumbDirController.text = await _storage.read(key: 'thumbnail_dir') ?? '';
  }

  Future<void> _saveSettings() async {
    await ref.read(apiClientProvider).setConfig(
      _baseUrlController.text,
      _tokenController.text,
    );
    await ref.read(offlineDbProvider).setConfig(
      dbPath: _dbPathController.text,
      thumbnailDir: _thumbDirController.text,
    );
    
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Settings saved')),
      );
      ref.read(connectionStatusProvider.notifier).checkConnection();
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('API Settings', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
          const SizedBox(height: 16),
          TextField(
            controller: _baseUrlController,
            decoration: const InputDecoration(
              labelText: 'Base URL',
              border: OutlineInputBorder(),
              hintText: 'http://192.168.1.100:7373',
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _tokenController,
            decoration: const InputDecoration(
              labelText: 'Bearer Token',
              border: OutlineInputBorder(),
            ),
            obscureText: true,
          ),
          const SizedBox(height: 32),
          const Text('Offline Settings', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
          const SizedBox(height: 16),
          TextField(
            controller: _dbPathController,
            decoration: const InputDecoration(
              labelText: 'Catalog DB Path',
              border: OutlineInputBorder(),
              hintText: '/path/to/tgwcatalog.db',
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _thumbDirController,
            decoration: const InputDecoration(
              labelText: 'Thumbnail Directory',
              border: OutlineInputBorder(),
              hintText: '/path/to/thumbnails',
            ),
          ),
          const SizedBox(height: 32),
          SizedBox(
            width: double.infinity,
            height: 50,
            child: ElevatedButton(
              onPressed: _saveSettings,
              child: const Text('Save Settings'),
            ),
          ),
          const SizedBox(height: 32),
          const Text('App Information', style: TextStyle(fontSize: 14, color: Colors.grey)),
          const Text('TGW App v1.0.0 (Phase B/C)', style: TextStyle(fontSize: 12, color: Colors.grey)),
        ],
      ),
    );
  }
}
