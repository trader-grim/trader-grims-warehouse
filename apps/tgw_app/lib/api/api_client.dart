import 'dart:io';

import 'package:dio/dio.dart';
import '../config/tgw_config.dart';
import '../models/models.dart';

class ApiClient {
  final Dio _dio = Dio();
  String _baseUrl = 'http://127.0.0.1:7373';
  String? _token;
  bool _initialized = false;

  ApiClient() {
    _dio.options.connectTimeout = const Duration(seconds: 5);
    _dio.options.receiveTimeout = const Duration(seconds: 10);
  }

  Future<void> ensureInitialized() async {
    if (_initialized) return;
    _baseUrl = await TgwConfig.read('base_url') ?? 'http://127.0.0.1:7373';
    _token = await TgwConfig.read('bearer_token');
    _updateDioOptions();
    _initialized = true;
  }

  void _updateDioOptions() {
    _dio.options.baseUrl = _baseUrl;
    if (_token != null && _token!.isNotEmpty) {
      _dio.options.headers['Authorization'] = 'Bearer $_token';
    } else {
      _dio.options.headers.remove('Authorization');
    }
  }

  Future<void> setConfig(String baseUrl, String token) async {
    _baseUrl = baseUrl;
    _token = token;
    await TgwConfig.write('base_url', baseUrl);
    await TgwConfig.write('bearer_token', token);
    _updateDioOptions();
    _initialized = true;
  }

  Future<bool> checkConnection() async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/queue/status');
      return response.statusCode == 200;
    } catch (e) {
      return false;
    }
  }

  Future<ApiResponse<List<ItemSummary>>> getItems({
    String? search,
    String? location,
    String? statusFilter,
    int limit = 200,
    int offset = 0,
  }) async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/items', queryParameters: {
        if (search != null && search.isNotEmpty) 'search': search,
        if (location != null && location.isNotEmpty) 'location': location,
        if (statusFilter != null && statusFilter.isNotEmpty) 'status_filter': statusFilter,
        'limit': limit,
        'offset': offset,
      });
      
      if (response.statusCode == 200) {
        final List<dynamic> itemsJson = response.data['items'] ?? [];
        final items = itemsJson.map((j) => ItemSummary.fromJson(j)).toList();
        return ApiResponse(ok: true, data: items);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<ItemDetail>> getItem(String sku) async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/items/$sku');
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: ItemDetail.fromJson(response.data));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<QueueStatus>> getQueueStatus() async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/queue/status');
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: QueueStatus.fromJson(response.data));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<List<String>>> getLocations() async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/locations');
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: List<String>.from(response.data['locations'] ?? []));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<List<Map<String, dynamic>>>> getCategoryGroups() async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/category-groups');
      if (response.statusCode == 200) {
        final List<dynamic> groups = response.data['groups'] ?? [];
        return ApiResponse(ok: true, data: List<Map<String, dynamic>>.from(groups));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<void>> patchItem(String sku, Map<String, dynamic> fields) async {
    await ensureInitialized();
    try {
      final response = await _dio.patch('/api/items/$sku', data: {'fields': fields});
      if (response.statusCode == 200) {
        return ApiResponse(ok: true);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<String>> performAction(String sku, String action, {Map<String, dynamic>? options}) async {
    await ensureInitialized();
    try {
      final response = await _dio.post('/api/items/$sku/action', data: {
        'action': action,
        if (options != null) 'options': options,
      });
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: response.data['job_id']);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<List<Map<String, dynamic>>>> getEbayAspects(String categoryId) async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/ebay/aspects/$categoryId');
      if (response.statusCode == 200) {
        final List<dynamic> aspects = response.data['aspects'] ?? [];
        return ApiResponse(ok: true, data: List<Map<String, dynamic>>.from(aspects));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<void>> setItemTemplate(String sku, String templateKey) async {
    await ensureInitialized();
    try {
      final response = await _dio.post('/api/items/$sku/set-template', data: {
        'template_key': templateKey,
      });
      if (response.statusCode == 200) {
        return ApiResponse(ok: true);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<List<ReviewQueueItem>>> getReviewQueue() async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/items/review-queue');
      if (response.statusCode == 200) {
        final List<dynamic> items = response.data['items'] ?? [];
        return ApiResponse(
          ok: true,
          data: items.map((j) => ReviewQueueItem.fromJson(j as Map<String, dynamic>)).toList(),
        );
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<List<dynamic>>> getHintTrail(String sku) async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/items/$sku/hint-trail');
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: response.data['history'] ?? []);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }
  Future<ApiResponse<String>> uploadToInbox(File file) async {
    await ensureInitialized();
    try {
      final formData = FormData.fromMap({
        'file': await MultipartFile.fromFile(file.path, filename: file.uri.pathSegments.last),
      });
      final response = await _dio.post('/api/inbox/upload', data: formData);
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: response.data['filename'] as String?);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> bulkAction(List<String> skus, String action) async {
    await ensureInitialized();
    try {
      final response = await _dio.post('/api/bulk/action', data: {'skus': skus, 'action': action});
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: Map<String, dynamic>.from(response.data));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<List<PipelineJob>>> getPipelineJobs() async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/pipeline/jobs');
      if (response.statusCode == 200) {
        final List<dynamic> jobs = response.data['jobs'] ?? [];
        return ApiResponse(ok: true, data: jobs.map((j) => PipelineJob.fromJson(j as Map<String, dynamic>)).toList());
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<String>> requeueJob(String jobId) async {
    await ensureInitialized();
    try {
      final response = await _dio.post('/api/jobs/$jobId/requeue');
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: response.data['new_job_id'] as String?);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<void>> cancelJob(String jobId) async {
    await ensureInitialized();
    try {
      final response = await _dio.post('/api/jobs/$jobId/cancel');
      if (response.statusCode == 200) {
        return ApiResponse(ok: true);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<void>> reportJobToAdmin(String jobId, String queueName, String? errorDetail) async {
    await ensureInitialized();
    try {
      final msg = 'ADMIN-REPORT: dead_letter job $jobId in queue $queueName — ${errorDetail ?? "no error detail"}';
      final response = await _dio.post('/api/suggest', data: {'text': msg});
      if (response.statusCode == 200) {
        return ApiResponse(ok: true);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<void>> deleteItem(String sku) async {
    await ensureInitialized();
    try {
      final response = await _dio.delete('/api/items/$sku');
      if (response.statusCode == 200) {
        return ApiResponse(ok: true);
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  // The Flutter surface projects the same HTTP-backed PlanAuthority records
  // as web and CLI clients. It deliberately has no `/consume` method: only a
  // separately authenticated registered executor can redeem an approval.
  Future<ApiResponse<List<Map<String, dynamic>>>> getPlanAuthorityRequests({int limit = 100}) async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/plan-authority/requests', queryParameters: {'limit': limit});
      if (response.statusCode == 200) {
        final List<dynamic> requests = response.data['requests'] ?? [];
        return ApiResponse(ok: true, data: requests.map((value) => Map<String, dynamic>.from(value as Map)).toList());
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> getPlanAuthorityRequest(String requestId) async {
    await ensureInitialized();
    try {
      final response = await _dio.get('/api/plan-authority/requests/$requestId');
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: Map<String, dynamic>.from(response.data));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> createPlanAuthorityRequest(Map<String, dynamic> request) async {
    await ensureInitialized();
    try {
      final response = await _dio.post('/api/plan-authority/requests', data: request);
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: Map<String, dynamic>.from(response.data));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> decidePlanAuthorityRequest(
    String requestId, {
    required String kind,
    required String reason,
  }) async {
    await ensureInitialized();
    if (!{'approve', 'hold', 'reconcile'}.contains(kind) || reason.trim().isEmpty) {
      return ApiResponse(ok: false, error: 'A valid authority decision and reason are required');
    }
    try {
      final response = await _dio.post(
        '/api/plan-authority/requests/$requestId/decisions',
        data: {'kind': kind, 'reason': reason},
      );
      if (response.statusCode == 200) {
        return ApiResponse(ok: true, data: Map<String, dynamic>.from(response.data));
      }
      return ApiResponse(ok: false, error: 'Status: ${response.statusCode}');
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  String getThumbnailUrl(String sku) {
    return '$_baseUrl/thumb/$sku';
  }

  String mediaUrl(String path) => '$_baseUrl$path';

  String get baseUrl => _baseUrl;

  Future<ApiResponse<void>> downloadSnapshot(String destPath) async {
    await ensureInitialized();
    try {
      await _dio.download(
        '/api/catalog/snapshot',
        destPath,
        options: Options(receiveTimeout: const Duration(minutes: 5)),
      );
      return ApiResponse(ok: true);
    } catch (e) {
      return ApiResponse(ok: false, error: e.toString());
    }
  }

  Map<String, String> get authHeaders => {
    if (_token != null && _token!.isNotEmpty) 'Authorization': 'Bearer $_token',
  };
}
