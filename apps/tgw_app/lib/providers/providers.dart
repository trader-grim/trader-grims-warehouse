import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../api/api_client.dart';
import '../db/offline_db.dart';
import '../models/models.dart';
import '../repository/repository.dart';

enum ConnectionStatus { online, offline, error }

final apiClientProvider = Provider((ref) => ApiClient());
final offlineDbProvider = Provider((ref) => OfflineDb());

final repositoryProvider = Provider((ref) => TgwRepository(
  apiClient: ref.watch(apiClientProvider),
  offlineDb: ref.watch(offlineDbProvider),
  ref: ref,
));

final connectionStatusProvider = StateNotifierProvider<ConnectionStatusNotifier, ConnectionStatus>((ref) {
  return ConnectionStatusNotifier(ref.watch(apiClientProvider));
});

class ConnectionStatusNotifier extends StateNotifier<ConnectionStatus> {
  final ApiClient _apiClient;
  
  ConnectionStatusNotifier(this._apiClient) : super(ConnectionStatus.offline) {
    checkConnection();
  }

  Future<void> checkConnection() async {
    final isOnline = await _apiClient.checkConnection();
    state = isOnline ? ConnectionStatus.online : ConnectionStatus.offline;
  }
}

final queueStatusProvider = FutureProvider<QueueStatus?>((ref) async {
  final api = ref.watch(apiClientProvider);
  final response = await api.getQueueStatus();
  return response.data;
});

final categoryGroupsProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final api = ref.watch(apiClientProvider);
  final response = await api.getCategoryGroups();
  return response.data ?? [];
});

final itemDetailProvider = FutureProvider.family<ItemDetail?, String>((ref, sku) async {
  return await ref.watch(repositoryProvider).getItem(sku);
});
