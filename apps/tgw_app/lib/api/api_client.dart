import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../models/models.dart';

class ApiClient {
  final Dio _dio = Dio();
  final FlutterSecureStorage _storage = const FlutterSecureStorage();
  
  String _baseUrl = 'http://127.0.0.1:7373';
  String? _token;
  bool _initialized = false;

  ApiClient() {
    _dio.options.connectTimeout = const Duration(seconds: 5);
    _dio.options.receiveTimeout = const Duration(seconds: 10);
  }

  Future<void> ensureInitialized() async {
    if (_initialized) return;
    _baseUrl = await _storage.read(key: 'base_url') ?? 'http://127.0.0.1:7373';
    _token = await _storage.read(key: 'bearer_token');
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
    await _storage.write(key: 'base_url', value: baseUrl);
    await _storage.write(key: 'bearer_token', value: token);
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
  String getThumbnailUrl(String sku) {
    return '$_baseUrl/api/items/$sku/thumbnail';
  }

  Map<String, String> get authHeaders => {
    if (_token != null && _token!.isNotEmpty) 'Authorization': 'Bearer $_token',
  };
}
