import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../api/api_client.dart';
import '../db/offline_db.dart';
import '../models/models.dart';
import '../providers/providers.dart';

class TgwRepository {
  final ApiClient apiClient;
  final OfflineDb offlineDb;
  final Ref ref;

  TgwRepository({required this.apiClient, required this.offlineDb, required this.ref});

  Future<List<ItemSummary>> getItems({
    String? search,
    String? location,
    String? statusFilter,
    int limit = 200,
    int offset = 0,
  }) async {
    final status = ref.read(connectionStatusProvider);
    if (status == ConnectionStatus.online) {
      final response = await apiClient.getItems(
        search: search,
        location: location,
        statusFilter: statusFilter,
        limit: limit,
        offset: offset,
      );
      if (response.ok) return response.data ?? [];
    }
    
    return await offlineDb.getItems(
      search: search,
      location: location,
      statusFilter: statusFilter,
      limit: limit,
      offset: offset,
    );
  }

  Future<ItemDetail?> getItem(String sku) async {
    final status = ref.read(connectionStatusProvider);
    if (status == ConnectionStatus.online) {
      final response = await apiClient.getItem(sku);
      if (response.ok) return response.data;
    }
    return await offlineDb.getItem(sku);
  }

  Future<List<String>> getLocations() async {
    final status = ref.read(connectionStatusProvider);
    if (status == ConnectionStatus.online) {
      final response = await apiClient.getLocations();
      if (response.ok) return response.data ?? [];
    }
    return await offlineDb.getLocations();
  }
}
