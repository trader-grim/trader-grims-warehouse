class ApiResponse<T> {
  final bool ok;
  final String? error;
  final T? data;

  ApiResponse({required this.ok, this.error, this.data});

  factory ApiResponse.fromJson(Map<String, dynamic> json, T Function(Map<String, dynamic>) fromJsonT) {
    return ApiResponse(
      ok: json['ok'] ?? false,
      error: json['error'],
      data: json['ok'] == true ? fromJsonT(json) : null,
    );
  }
}

class ItemSummary {
  final String sku;
  final String title;
  final String location;
  final String status;
  final String price;
  final String qty;
  final String? image;
  final String? ebayListingId;
  final String? ebayOfferId;
  final String? ebayReadyAt;
  final bool hasDraft;

  ItemSummary({
    required this.sku,
    required this.title,
    required this.location,
    required this.status,
    required this.price,
    required this.qty,
    this.image,
    this.ebayListingId,
    this.ebayOfferId,
    this.ebayReadyAt,
    this.hasDraft = false,
  });

  factory ItemSummary.fromJson(Map<String, dynamic> json) {
    return ItemSummary(
      sku: json['sku'] ?? '',
      title: json['title'] ?? '',
      location: json['location'] ?? 'Unknown',
      status: json['status'] ?? 'Unknown',
      price: json['price']?.toString() ?? '',
      qty: json['qty']?.toString() ?? '0',
      image: json['image'] as String?,
      ebayListingId: json['ebay_listing_id'] as String?,
      ebayOfferId: json['ebay_offer_id'] as String?,
      ebayReadyAt: json['ebay_ready_at'] as String?,
      hasDraft: (json['has_draft'] as int? ?? 0) == 1,
    );
  }

  Map<String, dynamic> toJson() => {
    'sku': sku,
    'title': title,
    'location': location,
    'status': status,
    'price': price,
    'qty': qty,
    'image': image,
    'ebay_listing_id': ebayListingId,
    'ebay_offer_id': ebayOfferId,
    'ebay_ready_at': ebayReadyAt,
    'has_draft': hasDraft ? 1 : 0,
  };
}

class ItemDetail {
  final String sku;
  final Map<String, dynamic> data;
  final List<String> images;
  final List<String> videos;
  final List<dynamic> queueJobs;

  ItemDetail({
    required this.sku,
    required this.data,
    required this.images,
    required this.videos,
    required this.queueJobs,
  });

  factory ItemDetail.fromJson(Map<String, dynamic> json) {
    return ItemDetail(
      sku: json['item']?['sku'] ?? '',
      data: json['item'] ?? {},
      images: List<String>.from(json['images'] ?? []),
      videos: List<String>.from(json['videos'] ?? []),
      queueJobs: json['queue_jobs'] ?? [],
    );
  }
}

class QueueStatus {
  final Map<String, Map<String, int>> queues;

  QueueStatus({required this.queues});

  factory QueueStatus.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> qJson = json['queues'] ?? {};
    final Map<String, Map<String, int>> parsed = {};
    qJson.forEach((key, value) {
      if (value is Map) {
        parsed[key] = Map<String, int>.from(value.map((k, v) => MapEntry(k, v as int)));
      }
    });
    return QueueStatus(queues: parsed);
  }
}

class PipelineJob {
  final String jobId;
  final String queueName;
  final String state;
  final String? sku;
  final String? startedAt;
  final String? finishedAt;
  final String? createdAt;
  final String? errorDetail;
  final int attemptCount;
  final int maxAttempts;

  PipelineJob({
    required this.jobId,
    required this.queueName,
    required this.state,
    this.sku,
    this.startedAt,
    this.finishedAt,
    this.createdAt,
    this.errorDetail,
    required this.attemptCount,
    required this.maxAttempts,
  });

  factory PipelineJob.fromJson(Map<String, dynamic> json) {
    return PipelineJob(
      jobId: json['job_id'] ?? '',
      queueName: json['queue_name'] ?? '',
      state: json['state'] ?? '',
      sku: json['sku'] as String?,
      startedAt: json['started_at'] as String?,
      finishedAt: json['finished_at'] as String?,
      createdAt: json['created_at'] as String?,
      errorDetail: json['error_detail'] as String?,
      attemptCount: json['attempt_count'] ?? 0,
      maxAttempts: json['max_attempts'] ?? 3,
    );
  }

  /// 'transient' | 'permanent' | 'unknown'
  String get errorClass {
    final e = (errorDetail ?? '').toLowerCase();
    if (e.isEmpty) return 'unknown';
    if (e.contains('timeout') ||
        e.contains('connection') ||
        e.contains('503') ||
        e.contains('502') ||
        e.contains('rate limit') ||
        e.contains('too many') ||
        e.contains('temporarily') ||
        e.contains('econnrefused') ||
        e.contains('network')) { return 'transient'; }
    if (e.contains('not found') ||
        e.contains('invalid') ||
        e.contains('forbidden') ||
        e.contains(' 404') ||
        e.contains(' 403') ||
        e.contains(' 400') ||
        e.contains(' 401') ||
        e.contains('unauthorized') ||
        e.contains('does not exist') ||
        e.contains('no such') ||
        e.contains('missing')) { return 'permanent'; }
    return 'unknown';
  }

  static const Map<String, int> _expectedDurationSecs = {
    'catalog_rebuild': 120,
    'thumbnail_gen': 60,
    'ai_identify': 600,
    'ebay_draft': 120,
    'ebay_upload': 60,
    'ebay_price': 120,
    'ebay_stage': 60,
    'ebay_publish': 60,
    'ebay_dole': 30,
    'ebay_sync': 300,
    'ebay_legacy_sync': 300,
    'token_refresh': 60,
    'pm_intake': 120,
    'bundle_intake': 60,
    'multi_intake': 120,
    'plan_render': 60,
    'echo': 10,
  };

  bool get isStuck {
    if (state != 'running' && state != 'leased') return false;
    final sa = startedAt;
    if (sa == null) return false;
    try {
      final elapsed = DateTime.now().difference(DateTime.parse(sa)).inSeconds;
      final expected = _expectedDurationSecs[queueName] ?? 300;
      return elapsed > expected * 2;
    } catch (_) {
      return false;
    }
  }

  Duration? get elapsed {
    final ref = startedAt ?? createdAt;
    if (ref == null) return null;
    try {
      return DateTime.now().difference(DateTime.parse(ref));
    } catch (_) {
      return null;
    }
  }
}

class ReviewQueueItem {
  final String sku;
  final String title;
  final String location;
  final String status;
  final double? price;
  final String condition;
  final String conditionLabel;
  final String conditionDescription;
  final String categoryId;
  final String categoryName;
  final String shippingProfile;
  final Map<String, dynamic> quality;
  final int? aspectsRequiredTotal;
  final int? aspectsRequiredFilled;

  ReviewQueueItem({
    required this.sku,
    required this.title,
    required this.location,
    required this.status,
    this.price,
    required this.condition,
    required this.conditionLabel,
    required this.conditionDescription,
    required this.categoryId,
    required this.categoryName,
    required this.shippingProfile,
    required this.quality,
    this.aspectsRequiredTotal,
    this.aspectsRequiredFilled,
  });

  factory ReviewQueueItem.fromJson(Map<String, dynamic> json) {
    return ReviewQueueItem(
      sku: json['sku'] ?? '',
      title: json['title'] ?? '',
      location: json['location'] ?? '',
      status: json['status'] ?? '',
      price: (json['price'] as num?)?.toDouble(),
      condition: json['condition'] ?? '',
      conditionLabel: json['condition_label'] ?? '',
      conditionDescription: json['condition_description'] ?? '',
      categoryId: json['category_id'] ?? '',
      categoryName: json['category_name'] ?? '',
      shippingProfile: json['shipping_profile'] ?? '',
      quality: (json['quality'] as Map?)?.cast<String, dynamic>() ?? {},
      aspectsRequiredTotal: json['aspects_required_total'] as int?,
      aspectsRequiredFilled: json['aspects_required_filled'] as int?,
    );
  }

  int? get qualityScore => quality['score'] as int?;

  String get aspectsSummary {
    final total = aspectsRequiredTotal;
    final filled = aspectsRequiredFilled;
    if (total == null || filled == null) return '';
    return '$filled/$total req.';
  }
}

class CategoryGroup {
  final String name;
  final String? sizeClass;
  final List<int> ebayCategories;
  final String? aiHint;
  final Map<String, dynamic>? pricing;

  CategoryGroup({
    required this.name,
    this.sizeClass,
    required this.ebayCategories,
    this.aiHint,
    this.pricing,
  });

  factory CategoryGroup.fromJson(Map<String, dynamic> json) {
    return CategoryGroup(
      name: json['name'] ?? '',
      sizeClass: json['size_class'],
      ebayCategories: List<int>.from(json['ebay_categories'] ?? []),
      aiHint: json['ai_hint'],
      pricing: json['pricing'],
    );
  }
}

class FlushResult {
  final int sent;
  final int failed;
  const FlushResult({required this.sent, required this.failed});
}
