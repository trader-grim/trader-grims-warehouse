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
