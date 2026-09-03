class ApiResponse<T> {
  final bool ok;
  final String? error;
  final T? data;

  ApiResponse({required this.ok, this.error, this.data});

  factory ApiResponse.fromJson(
    Map<String, dynamic> json,
    T Function(Map<String, dynamic>) fromJsonT,
  ) {
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
    final item = json['item'] as Map<String, dynamic>? ?? {};
    final sku = item['sku'] as String? ?? '';
    final rawImages = List<String>.from(item['_images'] ?? []);
    final rawVideos = List<String>.from(item['_videos'] ?? []);
    return ItemDetail(
      sku: sku,
      data: item,
      images: rawImages.map((f) => '/media/$sku/$f').toList(),
      videos: rawVideos.map((f) => '/media/$sku/$f').toList(),
      queueJobs: item['_queue_jobs'] ?? [],
    );
  }
}

class OperatorCommandDescriptor {
  final String id;
  final String label;
  final bool enabled;
  final String? reason;
  final String authorityScope;
  final Map<String, dynamic> inputSchema;
  final String valueSource;
  final List<String> views;
  final String tone;
  final String group;
  final OperatorCommandConfirmation? confirmation;

  OperatorCommandDescriptor({
    required this.id,
    required this.label,
    required this.enabled,
    required this.reason,
    required this.authorityScope,
    required this.inputSchema,
    required this.valueSource,
    required this.views,
    this.tone = 'neutral',
    this.group = '',
    this.confirmation,
  });

  factory OperatorCommandDescriptor.fromJson(Map<String, dynamic> json) {
    return OperatorCommandDescriptor(
      id: json['id'] as String? ?? '',
      label: json['label'] as String? ?? '',
      enabled: json['enabled'] == true,
      reason: json['reason'] as String?,
      authorityScope: json['authority_scope'] as String? ?? '',
      inputSchema: Map<String, dynamic>.from(
        json['input_schema'] as Map? ?? const {},
      ),
      valueSource: json['value_source'] as String? ?? '',
      views: List<String>.from(json['views'] as List? ?? const []),
      tone: json['tone'] as String? ?? 'neutral',
      group: json['group'] as String? ?? '',
      confirmation: OperatorCommandConfirmation.fromValue(json['confirmation']),
    );
  }
}

class OperatorCommandConfirmation {
  final String title;
  final String message;
  final String confirmLabel;

  const OperatorCommandConfirmation({
    required this.title,
    required this.message,
    required this.confirmLabel,
  });

  static OperatorCommandConfirmation? fromValue(dynamic value) {
    if (value is String && value.trim().isNotEmpty) {
      return OperatorCommandConfirmation(
        title: '',
        message: value,
        confirmLabel: '',
      );
    }
    if (value is! Map) return null;
    final json = Map<String, dynamic>.from(value);
    final message = (json['message'] ?? json['body'] ?? '').toString();
    if (message.trim().isEmpty) return null;
    return OperatorCommandConfirmation(
      title: (json['title'] ?? '').toString(),
      message: message,
      confirmLabel: (json['confirm_label'] ?? json['label'] ?? '').toString(),
    );
  }
}

class OperatorMediaDescriptor {
  final String kind;
  final String name;
  final String url;
  final int? position;
  final bool primary;

  const OperatorMediaDescriptor({
    required this.kind,
    required this.name,
    required this.url,
    required this.position,
    required this.primary,
  });

  factory OperatorMediaDescriptor.fromJson(Map<String, dynamic> json) {
    return OperatorMediaDescriptor(
      kind: json['kind'] as String? ?? 'image',
      name: json['name'] as String? ?? '',
      url: json['url'] as String? ?? '',
      position: (json['position'] as num?)?.toInt(),
      primary: json['primary'] == true,
    );
  }
}

class OperatorProviderMedia {
  final String source;
  final String status;
  final int count;
  final List<OperatorMediaDescriptor> items;

  const OperatorProviderMedia({
    required this.source,
    required this.status,
    required this.count,
    required this.items,
  });

  factory OperatorProviderMedia.fromJson(Map<String, dynamic> json) {
    final items = _mapList(
      json['items'],
    ).map(OperatorMediaDescriptor.fromJson).toList(growable: false);
    return OperatorProviderMedia(
      source: json['source'] as String? ?? '',
      status: json['status'] as String? ?? '',
      count: (json['count'] as num?)?.toInt() ?? items.length,
      items: items,
    );
  }

  static const empty = OperatorProviderMedia(
    source: '',
    status: '',
    count: 0,
    items: [],
  );
}

class OperatorFieldOption {
  final dynamic value;
  final String label;

  const OperatorFieldOption({required this.value, required this.label});

  factory OperatorFieldOption.fromValue(dynamic value) {
    if (value is Map) {
      final option = Map<String, dynamic>.from(value);
      final optionValue = option['value'] ?? option['id'] ?? option['enum'];
      return OperatorFieldOption(
        value: optionValue,
        label:
            (option['label'] ?? option['name'] ?? optionValue ?? '').toString(),
      );
    }
    return OperatorFieldOption(value: value, label: value?.toString() ?? '');
  }
}

class OperatorFieldSelection {
  final dynamic value;
  final String label;
  final dynamic path;

  const OperatorFieldSelection({
    required this.value,
    required this.label,
    required this.path,
  });

  factory OperatorFieldSelection.fromJson(Map<String, dynamic> json) {
    return OperatorFieldSelection(
      value: json['value'],
      label: (json['label'] ?? json['name'] ?? '').toString(),
      path: json['path'],
    );
  }

  static const empty = OperatorFieldSelection(value: null, label: '', path: '');
}

class OperatorFieldDescriptor {
  final String name;
  final String type;
  final String label;
  final dynamic value;
  final bool nullable;
  final bool required;
  final String control;
  final String hint;
  final List<OperatorFieldOption> options;
  final Map<String, dynamic> lookup;
  final OperatorFieldSelection selection;
  final Map<String, dynamic> raw;

  const OperatorFieldDescriptor({
    required this.name,
    required this.type,
    required this.label,
    required this.value,
    required this.nullable,
    required this.required,
    required this.control,
    required this.hint,
    required this.options,
    required this.lookup,
    required this.selection,
    required this.raw,
  });

  factory OperatorFieldDescriptor.fromJson(
    String name,
    Map<String, dynamic> json,
  ) {
    return OperatorFieldDescriptor(
      name: name,
      type: json['type'] as String? ?? 'string',
      label: (json['label'] ?? name).toString(),
      value: json['value'],
      nullable: json['nullable'] == true,
      required: json['required'] == true,
      control: json['control'] as String? ?? '',
      hint: (json['hint'] ?? '').toString(),
      options: (json['options'] as List? ?? const [])
          .map(OperatorFieldOption.fromValue)
          .toList(growable: false),
      lookup: _mapping(json['lookup']),
      selection: json['selection'] is Map
          ? OperatorFieldSelection.fromJson(_mapping(json['selection']))
          : OperatorFieldSelection.empty,
      raw: json,
    );
  }
}

class OperatorAspectDescriptor {
  final String name;
  final dynamic value;
  final bool required;
  final List<String> allowedValues;
  final dynamic inventoryValue;
  final dynamic liveValue;
  final dynamic proposedValue;
  final bool custom;

  const OperatorAspectDescriptor({
    required this.name,
    required this.value,
    required this.required,
    required this.allowedValues,
    required this.inventoryValue,
    required this.liveValue,
    required this.proposedValue,
    required this.custom,
  });

  factory OperatorAspectDescriptor.fromJson(Map<String, dynamic> json) {
    return OperatorAspectDescriptor(
      name: json['name'] as String? ?? '',
      value: json['value'],
      required: json['required'] == true,
      allowedValues: (json['allowed_values'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
      inventoryValue: json['inventory_value'],
      liveValue: json['live_value'],
      proposedValue: json['proposed_value'],
      custom: json['custom'] == true,
    );
  }
}

class OperatorPricingSchema {
  final dynamic current;
  final dynamic target;
  final String source;
  final String pricedAt;
  final String categoryHint;
  final List<Map<String, dynamic>> comps;
  final Map<String, dynamic> raw;

  const OperatorPricingSchema({
    required this.current,
    required this.target,
    required this.source,
    required this.pricedAt,
    required this.categoryHint,
    required this.comps,
    required this.raw,
  });

  factory OperatorPricingSchema.fromJson(Map<String, dynamic> json) {
    return OperatorPricingSchema(
      current: json['current'],
      target: json['target'],
      source: (json['source'] ?? '').toString(),
      pricedAt: (json['priced_at'] ?? '').toString(),
      categoryHint: (json['category_hint'] ?? '').toString(),
      comps: _mapList(json['comps']),
      raw: json,
    );
  }
}

class OperatorFieldSchema {
  final Map<String, OperatorFieldDescriptor> itemFields;
  final Map<String, OperatorFieldDescriptor> listingFields;
  final OperatorFieldDescriptor category;
  final OperatorFieldDescriptor condition;
  final List<OperatorAspectDescriptor> aspects;
  final OperatorPricingSchema pricing;

  const OperatorFieldSchema({
    required this.itemFields,
    required this.listingFields,
    required this.category,
    required this.condition,
    required this.aspects,
    required this.pricing,
  });

  factory OperatorFieldSchema.fromJson(Map<String, dynamic> json) {
    Map<String, OperatorFieldDescriptor> fields(dynamic value) {
      return _mapping(value).map(
        (name, descriptor) => MapEntry(
          name,
          OperatorFieldDescriptor.fromJson(name, _mapping(descriptor)),
        ),
      );
    }

    return OperatorFieldSchema(
      itemFields: fields(json['item_fields']),
      listingFields: fields(json['listing_fields']),
      category: OperatorFieldDescriptor.fromJson(
        'category_id',
        _mapping(json['category']),
      ),
      condition: OperatorFieldDescriptor.fromJson(
        'condition_enum',
        _mapping(json['condition']),
      ),
      aspects: _mapList(
        json['aspects'],
      ).map(OperatorAspectDescriptor.fromJson).toList(growable: false),
      pricing: OperatorPricingSchema.fromJson(_mapping(json['pricing'])),
    );
  }
}

class OperatorPresentationFact {
  final String id;
  final String label;
  final dynamic value;
  final String format;
  final String tone;

  const OperatorPresentationFact({
    required this.id,
    required this.label,
    required this.value,
    required this.format,
    required this.tone,
  });

  factory OperatorPresentationFact.fromJson(Map<String, dynamic> json) {
    return OperatorPresentationFact(
      id: json['id'] as String? ?? '',
      label: json['label'] as String? ?? '',
      value: json['value'],
      format: json['format'] as String? ?? 'text',
      tone: json['tone'] as String? ?? 'neutral',
    );
  }
}

class OperatorPresentationHeader {
  final List<OperatorPresentationFact> facts;

  const OperatorPresentationHeader({required this.facts});

  factory OperatorPresentationHeader.fromJson(Map<String, dynamic> json) {
    return OperatorPresentationHeader(
      facts: _mapList(
        json['facts'],
      ).map(OperatorPresentationFact.fromJson).toList(growable: false),
    );
  }
}

class OperatorPresentationRegion {
  final String id;
  final List<String> components;
  final List<String> sections;

  const OperatorPresentationRegion({
    required this.id,
    required this.components,
    required this.sections,
  });

  factory OperatorPresentationRegion.fromJson(Map<String, dynamic> json) {
    return OperatorPresentationRegion(
      id: json['id'] as String? ?? '',
      components: (json['components'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
      sections: (json['sections'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
    );
  }
}

class OperatorPresentationView {
  final String id;
  final String label;
  final bool isDefault;
  final String layout;
  final List<OperatorPresentationRegion> regions;

  const OperatorPresentationView({
    required this.id,
    required this.label,
    required this.isDefault,
    required this.layout,
    required this.regions,
  });

  factory OperatorPresentationView.fromJson(Map<String, dynamic> json) {
    final rawLayout = json['layout'];
    final layoutObject = _mapping(rawLayout);
    var regions = _mapList(json['regions']);
    if (regions.isEmpty) regions = _mapList(layoutObject['regions']);
    if (regions.isEmpty &&
        (json['components'] is List || json['sections'] is List)) {
      regions = [
        {
          'id': 'primary',
          'components': json['components'] as List? ?? const [],
          'sections': json['sections'] as List? ?? const [],
        },
      ];
    }
    return OperatorPresentationView(
      id: json['id'] as String? ?? '',
      label: json['label'] as String? ?? '',
      isDefault: json['default'] == true,
      layout: rawLayout is String
          ? rawLayout
          : (layoutObject['type'] ?? layoutObject['id'] ?? 'document')
              .toString(),
      regions: regions
          .map(OperatorPresentationRegion.fromJson)
          .toList(growable: false),
    );
  }
}

class OperatorPresentationSection {
  final String id;
  final String title;
  final String kind;
  final String description;
  final bool collapsed;
  final List<Map<String, dynamic>> rows;
  final List<Map<String, dynamic>> columns;
  final dynamic value;

  const OperatorPresentationSection({
    required this.id,
    required this.title,
    required this.kind,
    required this.description,
    required this.collapsed,
    required this.rows,
    required this.columns,
    required this.value,
  });

  factory OperatorPresentationSection.fromJson(Map<String, dynamic> json) {
    return OperatorPresentationSection(
      id: json['id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      kind: json['kind'] as String? ?? 'properties',
      description: json['description'] as String? ?? '',
      collapsed: json['collapsed'] == true,
      rows: _mapList(json['rows']),
      columns: _mapList(json['columns']),
      value: json['value'],
    );
  }
}

class OperatorPricingPresentation {
  final String id;
  final String title;
  final String commandId;
  final String searchTerms;
  final String searchTermsSource;
  final String requestedSearchTerms;
  final String lastSuccessfulSearchTerms;
  final dynamic suggestedPrice;
  final List<OperatorPresentationFact> rows;
  final List<String> detailsSections;
  final String detailsTarget;
  final List<OperatorResearchLink> researchLinks;
  final String researchNote;
  final Map<String, dynamic> raw;

  const OperatorPricingPresentation({
    required this.id,
    required this.title,
    required this.commandId,
    required this.searchTerms,
    required this.searchTermsSource,
    required this.requestedSearchTerms,
    required this.lastSuccessfulSearchTerms,
    required this.suggestedPrice,
    required this.rows,
    required this.detailsSections,
    required this.detailsTarget,
    required this.researchLinks,
    required this.researchNote,
    required this.raw,
  });

  factory OperatorPricingPresentation.fromJson(Map<String, dynamic> json) {
    return OperatorPricingPresentation(
      id: (json['id'] ?? '').toString(),
      title: json['title'] as String? ?? 'Pricing',
      commandId: (json['command_id'] ?? '').toString(),
      searchTerms: (json['search_terms'] ?? '').toString(),
      searchTermsSource: (json['search_terms_source'] ?? '').toString(),
      requestedSearchTerms: (json['requested_search_terms'] ?? '').toString(),
      lastSuccessfulSearchTerms:
          (json['last_successful_search_terms'] ?? '').toString(),
      suggestedPrice: json['suggested_price'],
      rows: _mapList(
        json['rows'],
      ).map(OperatorPresentationFact.fromJson).toList(growable: false),
      detailsSections: (json['details_sections'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
      detailsTarget: (json['details_target'] ?? '').toString(),
      researchLinks: _mapList(
        json['research_links'],
      ).map(OperatorResearchLink.fromJson).toList(growable: false),
      researchNote: (json['research_note'] ?? '').toString(),
      raw: json,
    );
  }
}

class OperatorResearchLink {
  final String id;
  final String label;
  final String href;
  final bool external;

  const OperatorResearchLink({
    required this.id,
    required this.label,
    required this.href,
    required this.external,
  });

  factory OperatorResearchLink.fromJson(Map<String, dynamic> json) {
    return OperatorResearchLink(
      id: (json['id'] ?? '').toString(),
      label: (json['label'] ?? '').toString(),
      href: (json['href'] ?? '').toString(),
      external: json['external'] == true,
    );
  }
}

class OperatorActionMenuDescriptor {
  final String id;
  final String label;
  final List<String> commandIds;
  final String defaultCommandId;
  final List<String> views;

  const OperatorActionMenuDescriptor({
    required this.id,
    required this.label,
    required this.commandIds,
    required this.defaultCommandId,
    required this.views,
  });

  factory OperatorActionMenuDescriptor.fromJson(Map<String, dynamic> json) {
    return OperatorActionMenuDescriptor(
      id: (json['id'] ?? '').toString(),
      label: (json['label'] ?? '').toString(),
      commandIds: (json['command_ids'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
      defaultCommandId: (json['default_command_id'] ?? '').toString(),
      views: (json['views'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
    );
  }
}

class OperatorDataNavigationEntry {
  final String label;
  final String target;

  const OperatorDataNavigationEntry({
    required this.label,
    required this.target,
  });

  factory OperatorDataNavigationEntry.fromJson(Map<String, dynamic> json) {
    return OperatorDataNavigationEntry(
      label: (json['label'] ?? '').toString(),
      target: (json['target'] ?? '').toString(),
    );
  }
}

class OperatorPresentation {
  final String schema;
  final String title;
  final String subtitle;
  final OperatorPresentationHeader header;
  final OperatorPricingPresentation pricingContext;
  final List<OperatorActionMenuDescriptor> actionMenus;
  final List<OperatorDataNavigationEntry> dataNavigation;
  final String listingEditorId;
  final List<OperatorPresentationView> views;
  final List<OperatorPresentationSection> sections;
  final List<Map<String, dynamic>> alerts;

  const OperatorPresentation({
    required this.schema,
    required this.title,
    required this.subtitle,
    required this.header,
    required this.pricingContext,
    required this.actionMenus,
    required this.dataNavigation,
    required this.listingEditorId,
    required this.views,
    required this.sections,
    required this.alerts,
  });

  factory OperatorPresentation.fromJson(Map<String, dynamic> json) {
    return OperatorPresentation(
      schema: json['schema'] as String? ?? '',
      title: json['title'] as String? ?? '',
      subtitle: json['subtitle'] as String? ?? '',
      header: OperatorPresentationHeader.fromJson(_mapping(json['header'])),
      pricingContext: OperatorPricingPresentation.fromJson(
        _mapping(json['pricing_context']),
      ),
      actionMenus: _mapList(json['action_menus'])
          .map(OperatorActionMenuDescriptor.fromJson)
          .toList(growable: false),
      dataNavigation: _mapList(json['data_navigation'])
          .map(OperatorDataNavigationEntry.fromJson)
          .toList(growable: false),
      listingEditorId:
          (_mapping(json['listing_editor'])['id'] ?? '').toString(),
      views: _mapList(
        json['views'],
      ).map(OperatorPresentationView.fromJson).toList(growable: false),
      sections: _mapList(
        json['sections'],
      ).map(OperatorPresentationSection.fromJson).toList(growable: false),
      alerts: _mapList(json['alerts']),
    );
  }

  OperatorPresentationSection? section(String id) {
    for (final section in sections) {
      if (section.id == id) return section;
    }
    return null;
  }
}

class OperatorObjectView {
  final String entityId;
  final String objectGeneration;
  final String state;
  final List<String> reasons;
  final Map<String, dynamic> item;
  final Map<String, dynamic> listing;
  final Map<String, dynamic> fieldSchema;
  final List<OperatorCommandDescriptor> commands;
  final List<OperatorMediaDescriptor> media;
  final OperatorProviderMedia providerMedia;
  final OperatorFieldSchema publishedFields;
  final OperatorPresentation presentation;

  OperatorObjectView({
    required this.entityId,
    required this.objectGeneration,
    required this.state,
    required this.reasons,
    required this.item,
    required this.listing,
    required this.fieldSchema,
    required this.commands,
    required this.media,
    required this.providerMedia,
    required this.publishedFields,
    required this.presentation,
  });

  factory OperatorObjectView.fromJson(Map<String, dynamic> json) {
    final object = Map<String, dynamic>.from(json['object'] as Map? ?? json);
    final workflow = Map<String, dynamic>.from(
      object['workflow'] as Map? ?? const {},
    );
    final rawCommands = object['commands'] as List? ?? const [];
    final item = Map<String, dynamic>.from(object['item'] as Map? ?? const {});
    final fieldSchema = Map<String, dynamic>.from(
      object['field_schema'] as Map? ?? const {},
    );
    return OperatorObjectView(
      entityId: object['entity_id'] as String? ?? '',
      objectGeneration: object['object_generation'] as String? ?? '',
      state: workflow['state'] as String? ?? 'unknown',
      reasons: List<String>.from(workflow['reasons'] as List? ?? const []),
      item: item,
      listing: Map<String, dynamic>.from(object['listing'] as Map? ?? const {}),
      fieldSchema: fieldSchema,
      commands: rawCommands
          .map(
            (value) => OperatorCommandDescriptor.fromJson(
              Map<String, dynamic>.from(value as Map),
            ),
          )
          .toList(),
      media: _mapList(
        item['media'],
      ).map(OperatorMediaDescriptor.fromJson).toList(growable: false),
      providerMedia: item['provider_media'] is Map
          ? OperatorProviderMedia.fromJson(_mapping(item['provider_media']))
          : OperatorProviderMedia.empty,
      publishedFields: OperatorFieldSchema.fromJson(fieldSchema),
      presentation: OperatorPresentation.fromJson(
        _mapping(object['presentation']),
      ),
    );
  }
}

Map<String, dynamic> _mapping(dynamic value) {
  return value is Map ? Map<String, dynamic>.from(value) : const {};
}

List<Map<String, dynamic>> _mapList(dynamic value) {
  if (value is! List) return const [];
  return value
      .whereType<Map>()
      .map((entry) => Map<String, dynamic>.from(entry))
      .toList(growable: false);
}

class QueueStatus {
  final Map<String, Map<String, int>> queues;

  QueueStatus({required this.queues});

  factory QueueStatus.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> qJson = json['queues'] ?? {};
    final Map<String, Map<String, int>> parsed = {};
    qJson.forEach((key, value) {
      if (value is Map) {
        parsed[key] = Map<String, int>.from(
          value.map((k, v) => MapEntry(k, v as int)),
        );
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
        e.contains('network')) {
      return 'transient';
    }
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
        e.contains('missing')) {
      return 'permanent';
    }
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
