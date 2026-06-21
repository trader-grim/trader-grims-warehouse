import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../api/api_client.dart';
import '../db/offline_db.dart';
import '../db/outbox_db.dart';
import '../models/models.dart';
import '../providers/providers.dart';

class TgwRepository {
  final ApiClient apiClient;
  final OfflineDb offlineDb;
  final OutboxDb outboxDb;
  final Ref ref;

  TgwRepository({
    required this.apiClient,
    required this.offlineDb,
    required this.outboxDb,
    required this.ref,
  });

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

  Future<bool> patchItem(String sku, Map<String, dynamic> fields) async {
    final status = ref.read(connectionStatusProvider);
    if (status == ConnectionStatus.online) {
      final response = await apiClient.patchItem(sku, fields);
      if (response.ok) return true;
    }
    // Queue for later flush when offline or API call failed.
    await outboxDb.enqueue(sku, fields);
    return true;
  }

  Future<FlushResult> flushOutbox() async {
    final mutations = await outboxDb.pending();
    int sent = 0, failed = 0;
    for (final m in mutations) {
      await outboxDb.markAttempt(m.id);
      final response = await apiClient.patchItem(m.sku, m.fields);
      if (response.ok) {
        await outboxDb.remove(m.id);
        sent++;
      } else {
        failed++;
      }
    }
    return FlushResult(sent: sent, failed: failed);
  }

  Future<int> pendingMutations() => outboxDb.pendingCount();

  Future<String?> performAction(String sku, String action, {Map<String, dynamic>? options}) async {
    final response = await apiClient.performAction(sku, action, options: options);
    return response.data;
  }

  Future<List<Map<String, dynamic>>> getEbayAspects(String categoryId) async {
    final response = await apiClient.getEbayAspects(categoryId);
    return response.data ?? [];
  }

  Future<bool> setItemTemplate(String sku, String templateKey) async {
    final response = await apiClient.setItemTemplate(sku, templateKey);
    return response.ok;
  }

  Future<List<dynamic>> getHintTrail(String sku) async {
    final response = await apiClient.getHintTrail(sku);
    return response.data ?? [];
  }

  Future<List<Map<String, dynamic>>> getCategoryGroups() async {
    final response = await apiClient.getCategoryGroups();
    return response.data ?? [];
  }

  Future<String?> uploadToInbox(File file) async {
    final response = await apiClient.uploadToInbox(file);
    return response.ok ? response.data : null;
  }

  Future<List<ReviewQueueItem>> getReviewQueue() async {
    final response = await apiClient.getReviewQueue();
    return response.data ?? [];
  }

  Future<Map<String, dynamic>?> bulkAction(List<String> skus, String action) async {
    final response = await apiClient.bulkAction(skus, action);
    return response.ok ? response.data : null;
  }
}
