import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../api/api_client.dart';
import '../db/offline_db.dart';
import '../models/models.dart';
import '../repository/repository.dart';
import '../services/catalog_sync_service.dart';

enum ConnectionStatus { online, offline, error }

final apiClientProvider = Provider((ref) => ApiClient());
final offlineDbProvider = Provider((ref) => OfflineDb());

final catalogSyncProvider = Provider(
  (ref) => CatalogSyncService(
    ref.watch(apiClientProvider),
    ref.watch(offlineDbProvider),
  ),
);

final repositoryProvider = Provider(
  (ref) => TgwRepository(
    apiClient: ref.watch(apiClientProvider),
    offlineDb: ref.watch(offlineDbProvider),
    ref: ref,
  ),
);

final connectionStatusProvider =
    StateNotifierProvider<ConnectionStatusNotifier, ConnectionStatus>((ref) {
      return ConnectionStatusNotifier(ref.watch(apiClientProvider), ref);
    });

class ConnectionStatusNotifier extends StateNotifier<ConnectionStatus> {
  final ApiClient _apiClient;
  final Ref _ref;

  ConnectionStatusNotifier(this._apiClient, this._ref)
    : super(ConnectionStatus.offline) {
    checkConnection();
  }

  Future<void> checkConnection() async {
    final isOnline = await _apiClient.checkConnection();
    state = isOnline ? ConnectionStatus.online : ConnectionStatus.offline;

    if (isOnline) {
      // Sync catalog snapshot on every transition to online.
      _ref.read(catalogSyncProvider).sync();
      // Operator-object mutations require a fresh online generation and are
      // never replayed from an offline client outbox.
    }
  }
}

final queueStatusProvider = FutureProvider<QueueStatus?>((ref) async {
  final api = ref.watch(apiClientProvider);
  final response = await api.getQueueStatus();
  return response.data;
});

final categoryGroupsProvider = FutureProvider<List<Map<String, dynamic>>>((
  ref,
) async {
  final api = ref.watch(apiClientProvider);
  final response = await api.getCategoryGroups();
  return response.data ?? [];
});

final itemDetailProvider = FutureProvider.family<ItemDetail?, String>((
  ref,
  sku,
) async {
  return await ref.watch(repositoryProvider).getItem(sku);
});

final operatorObjectProvider =
    FutureProvider.family<OperatorObjectView?, String>((ref, sku) async {
      final response = await ref
          .watch(apiClientProvider)
          .getOperatorObject(sku);
      if (!response.ok) {
        throw StateError(response.error ?? 'Operator object unavailable');
      }
      return response.data;
    });

final pipelineJobsProvider = FutureProvider<List<PipelineJob>>((ref) async {
  final api = ref.watch(apiClientProvider);
  final response = await api.getPipelineJobs();
  return response.data ?? [];
});
