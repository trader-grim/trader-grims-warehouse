import 'dart:io';

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../models/models.dart';
import '../../providers/providers.dart';
import 'operator_submission_values.dart';

class ItemScreen extends ConsumerWidget {
  final String? sku;

  const ItemScreen({super.key, this.sku});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final selectedSku = sku;
    if (selectedSku == null) {
      return const Center(child: Text('Select an item from Browse tab'));
    }

    final connection = ref.watch(connectionStatusProvider);
    if (connection != ConnectionStatus.online) {
      return ref.watch(itemDetailProvider(selectedSku)).when(
            data: (item) => item == null
                ? const Center(child: Text('Offline item not found'))
                : _OfflineItemView(item: item),
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) =>
                Center(child: Text('Offline snapshot unavailable: $error')),
          );
    }

    return ref.watch(operatorObjectProvider(selectedSku)).when(
          data: (object) => object == null
              ? const Center(child: Text('Published item not found'))
              : _PublishedItemView(object: object),
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, _) => _PublishedObjectError(
            error: error,
            onRetry: () => ref.invalidate(operatorObjectProvider(selectedSku)),
          ),
        );
  }
}

class _PublishedObjectError extends StatelessWidget {
  final Object error;
  final VoidCallback onRetry;

  const _PublishedObjectError({required this.error, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560),
        child: Card(
          margin: const EdgeInsets.all(20),
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.cloud_off_outlined,
                  size: 38,
                  color: Theme.of(context).colorScheme.error,
                ),
                const SizedBox(height: 12),
                const Text(
                  'Current operator object unavailable',
                  style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18),
                ),
                const SizedBox(height: 8),
                const Text(
                  'Editing and commands are held until the API publishes a current generation.',
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 8),
                Text(
                  error.toString(),
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
                const SizedBox(height: 14),
                FilledButton.icon(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh),
                  label: const Text('Retry'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _OfflineItemView extends ConsumerWidget {
  final ItemDetail item;

  const _OfflineItemView({required this.item});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final localPath =
        ref.read(offlineDbProvider).getLocalThumbnailPath(item.sku);
    final facts = <MapEntry<String, dynamic>>[
      MapEntry('Status', item.data['status']),
      MapEntry('Location', item.data['location']),
      MapEntry('Price', item.data['price']),
      MapEntry('Quantity', item.data['qty']),
      MapEntry('Condition', item.data['condition']),
    ].where((entry) => _hasValue(entry.value)).toList(growable: false);

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          color: Theme.of(context).colorScheme.surfaceContainerHighest,
          child: const ListTile(
            leading: Icon(Icons.offline_bolt_outlined),
            title: Text('Offline read-only snapshot'),
            subtitle: Text(
              'Reconnect to load the current published views, fields, and commands. No offline mutation is queued.',
            ),
          ),
        ),
        const SizedBox(height: 10),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    item.sku,
                    style: Theme.of(
                      context,
                    ).textTheme.labelMedium?.copyWith(fontFamily: 'monospace'),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    (item.data['title'] ?? 'Untitled item').toString(),
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                ],
              ),
            ),
            if (localPath != null && File(localPath).existsSync()) ...[
              const SizedBox(width: 12),
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.file(
                  File(localPath),
                  width: 108,
                  height: 108,
                  fit: BoxFit.cover,
                ),
              ),
            ],
          ],
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              children: facts
                  .map(
                    (entry) =>
                        _PropertyRow(label: entry.key, value: entry.value),
                  )
                  .toList(growable: false),
            ),
          ),
        ),
      ],
    );
  }
}

class _PublishedItemView extends ConsumerStatefulWidget {
  final OperatorObjectView object;

  const _PublishedItemView({required this.object});

  @override
  ConsumerState<_PublishedItemView> createState() => _PublishedItemViewState();
}

class _PublishedItemViewState extends ConsumerState<_PublishedItemView> {
  late Map<String, dynamic> _editorValues;
  late List<String> _mediaOrder;
  late String _pricingSearchTerms;
  late OperatorFieldDescriptor _conditionField;
  late List<OperatorAspectDescriptor> _aspects;
  late Set<String> _contextAspectNames;
  final Map<String, GlobalKey> _navigationTargets = {};
  bool _submitting = false;
  bool _categoryContextReady = true;

  OperatorObjectView get object => widget.object;

  @override
  void initState() {
    super.initState();
    _resetPublishedValues();
  }

  @override
  void didUpdateWidget(covariant _PublishedItemView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.object.objectGeneration != object.objectGeneration) {
      _resetPublishedValues();
    }
  }

  void _resetPublishedValues() {
    _editorValues = initialOperatorEditorValues(object);
    _pricingSearchTerms = object.presentation.pricingContext.searchTerms;
    _conditionField = object.publishedFields.condition;
    _aspects = List<OperatorAspectDescriptor>.from(
      object.publishedFields.aspects,
    );
    _contextAspectNames = _aspects
        .map((aspect) => aspect.name)
        .where((name) => name.isNotEmpty)
        .toSet();
    _categoryContextReady = true;
    _navigationTargets.clear();
    _mediaOrder = object.media
        .where((entry) => entry.kind.toLowerCase() == 'image')
        .map((entry) => entry.name)
        .where((name) => name.isNotEmpty)
        .toList(growable: true);
  }

  void _setFieldValue(String scope, String name, dynamic value) {
    final scopeValues = Map<String, dynamic>.from(
      _editorValues[scope] as Map? ?? const {},
    );
    scopeValues[name] = value;
    setState(() => _editorValues[scope] = scopeValues);
  }

  void _setAspectValue(String name, dynamic value) {
    final draft = Map<String, dynamic>.from(
      _editorValues['draft_listing'] as Map? ?? const {},
    );
    final specifics = Map<String, dynamic>.from(
      draft['item_specifics'] as Map? ?? const {},
    );
    specifics[name] = value;
    draft['item_specifics'] = specifics;
    setState(() => _editorValues['draft_listing'] = draft);
  }

  void _setCategoryContextReady(bool ready) {
    if (_categoryContextReady == ready) return;
    setState(() => _categoryContextReady = ready);
  }

  void _applyCategoryContext(Map<String, dynamic> publishedContext) {
    final context = publishedContext['context'] is Map
        ? Map<String, dynamic>.from(publishedContext['context'] as Map)
        : publishedContext;
    final draft = Map<String, dynamic>.from(
      _editorValues['draft_listing'] as Map? ?? const {},
    );
    final specifics = Map<String, dynamic>.from(
      draft['item_specifics'] as Map? ?? const {},
    );

    final priorAspects = {for (final aspect in _aspects) aspect.name: aspect};
    final contextAspects = _mapValues(context['aspects']);
    final mergedAspects = <OperatorAspectDescriptor>[];
    final seen = <String>{};
    for (final rawAspect in contextAspects) {
      final name = (rawAspect['name'] ?? '').toString();
      if (name.isEmpty) continue;
      seen.add(name);
      final prior = priorAspects[name];
      final value = specifics.containsKey(name)
          ? specifics[name]
          : rawAspect['value'] ?? prior?.value ?? '';
      specifics[name] = value?.toString() ?? '';
      mergedAspects.add(
        OperatorAspectDescriptor.fromJson({
          ...rawAspect,
          'name': name,
          'value': specifics[name],
          'inventory_value':
              rawAspect['inventory_value'] ?? prior?.inventoryValue,
          'live_value': rawAspect['live_value'] ?? prior?.liveValue,
          'proposed_value': rawAspect['proposed_value'] ?? prior?.proposedValue,
          'custom': rawAspect['custom'] == true,
        }),
      );
    }
    for (final prior in _aspects) {
      if (seen.contains(prior.name) ||
          !_hasAnyAspectValue(prior, specifics[prior.name])) {
        continue;
      }
      mergedAspects.add(prior);
      seen.add(prior.name);
    }

    var conditionValue = draft['condition_enum'];
    final rawConditions = _mapValues(context['conditions']);
    final conditionOptions = rawConditions
        .map(OperatorFieldOption.fromValue)
        .where((option) => option.value != null)
        .toList(growable: false);
    final allowedConditions =
        conditionOptions.map((option) => option.value.toString()).toSet();
    final remap = context['condition_remap'];
    if ((conditionValue?.toString().isNotEmpty ?? false) &&
        !allowedConditions.contains(conditionValue.toString()) &&
        remap is Map &&
        remap['enum'] != null) {
      conditionValue = remap['enum'].toString();
      draft['condition_enum'] = conditionValue;
    }

    draft['item_specifics'] = specifics;
    setState(() {
      _editorValues['draft_listing'] = draft;
      _conditionField = OperatorFieldDescriptor.fromJson(
        'condition_enum',
        {
          ..._conditionField.raw,
          'value': conditionValue,
          'options': rawConditions,
        },
      );
      _aspects = mergedAspects;
      _contextAspectNames = seen;
    });
  }

  void _moveMedia(int from, int to) {
    if (to < 0 || to >= _mediaOrder.length || from == to) return;
    setState(() {
      final name = _mediaOrder.removeAt(from);
      _mediaOrder.insert(to, name);
    });
  }

  Future<bool> _confirm(OperatorCommandDescriptor command) async {
    final confirmation = command.confirmation;
    if (confirmation == null) return true;
    return await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: Text(
              confirmation.title.isEmpty ? command.label : confirmation.title,
            ),
            content: Text(confirmation.message),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(dialogContext, true),
                child: Text(
                  confirmation.confirmLabel.isEmpty
                      ? command.label
                      : confirmation.confirmLabel,
                ),
              ),
            ],
          ),
        ) ??
        false;
  }

  Future<void> _execute(OperatorCommandDescriptor command) async {
    if (!command.enabled || _submitting) return;
    if (command.valueSource == 'editor' && !_categoryContextReady) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'Listing submission held until the published category context is current.',
          ),
        ),
      );
      return;
    }
    if (!await _confirm(command)) return;

    final Map<String, dynamic> values;
    try {
      values = buildOperatorCommandValues(
        object: object,
        command: command,
        editorValues: _editorValues,
        mediaOrder: _mediaOrder,
        pricingSearchTerms: _pricingSearchTerms,
        contextAspectNames: _contextAspectNames,
      );
    } on UnsupportedOperatorValueSource catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('${command.label} held: $error'),
          backgroundColor: Theme.of(context).colorScheme.error,
        ),
      );
      return;
    }

    setState(() => _submitting = true);
    final response = await ref.read(apiClientProvider).executeOperatorCommand(
          object.entityId,
          command,
          object.objectGeneration,
          values,
        );
    if (!mounted) return;
    setState(() => _submitting = false);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          response.ok
              ? '${command.label} accepted by the workflow.'
              : '${command.label} held: ${response.error ?? 'unknown error'}',
        ),
        backgroundColor:
            response.ok ? null : Theme.of(context).colorScheme.error,
      ),
    );
    ref.invalidate(operatorObjectProvider(object.entityId));
  }

  @override
  Widget build(BuildContext context) {
    final views = object.presentation.views;
    if (views.isEmpty) {
      return const _EmptyPublishedCard(
        message: 'The API published no item views for this generation.',
      );
    }
    final initialIndex = views.indexWhere((view) => view.isDefault);
    return DefaultTabController(
      key: ValueKey('${object.objectGeneration}:${views.length}'),
      length: views.length,
      initialIndex: initialIndex < 0 ? 0 : initialIndex,
      child: Column(
        children: [
          _buildHeader(context),
          TabBar(
            isScrollable: true,
            tabs: views
                .map(
                  (view) => Tab(
                    key: ValueKey('operator-tab-${view.id}'),
                    text: view.label,
                  ),
                )
                .toList(growable: false),
          ),
          Expanded(
            child: TabBarView(
              children: views
                  .map(
                    (view) => _buildView(
                      context,
                      view,
                      key: ValueKey('operator-view-${view.id}'),
                    ),
                  )
                  .toList(growable: false),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    final presentation = object.presentation;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      presentation.subtitle.isEmpty
                          ? object.entityId
                          : presentation.subtitle,
                      style: Theme.of(context).textTheme.labelMedium?.copyWith(
                            fontFamily: 'monospace',
                            color: Theme.of(context).colorScheme.outline,
                          ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      presentation.title.isEmpty
                          ? object.entityId
                          : presentation.title,
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Tooltip(
                message: object.objectGeneration,
                child: Text(
                  _shortGeneration(object.objectGeneration),
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        fontFamily: 'monospace',
                        color: Theme.of(context).colorScheme.outline,
                      ),
                ),
              ),
            ],
          ),
          if (presentation.header.facts.isNotEmpty) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 7,
              runSpacing: 7,
              children: presentation.header.facts
                  .map(
                    (fact) => Chip(
                      key: ValueKey('operator-fact-${fact.id}'),
                      avatar: fact.tone == 'error'
                          ? const Icon(Icons.error_outline, size: 16)
                          : null,
                      label: Text(
                        fact.label.isEmpty
                            ? _formatText(fact.value, fact.format)
                            : '${fact.label}: ${_formatText(fact.value, fact.format)}',
                      ),
                      side: BorderSide(color: _toneColor(context, fact.tone)),
                      visualDensity: VisualDensity.compact,
                    ),
                  )
                  .toList(growable: false),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildView(
    BuildContext context,
    OperatorPresentationView view, {
    Key? key,
  }) {
    if (view.regions.isEmpty) {
      return _EmptyPublishedCard(
        key: key,
        message: 'No regions are published for ${view.label}.',
      );
    }
    return LayoutBuilder(
      key: key,
      builder: (context, constraints) {
        final useColumns = constraints.maxWidth >= 960 &&
            (view.layout == 'dashboard' || view.layout == 'workstation');
        final regions = view.regions
            .map((region) => _buildRegion(context, view, region))
            .toList(growable: false);
        return SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: useColumns
              ? Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (var index = 0; index < regions.length; index++) ...[
                      if (index > 0) const SizedBox(width: 14),
                      Expanded(child: regions[index]),
                    ],
                  ],
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (var index = 0; index < regions.length; index++) ...[
                      if (index > 0) const SizedBox(height: 14),
                      regions[index],
                    ],
                  ],
                ),
        );
      },
    );
  }

  Widget _buildRegion(
    BuildContext context,
    OperatorPresentationView view,
    OperatorPresentationRegion region,
  ) {
    final children = <Widget>[];
    for (final component in region.components) {
      final rendered = _buildComponent(context, component, view.id);
      if (rendered != null) {
        if (children.isNotEmpty) children.add(const SizedBox(height: 12));
        children.add(rendered);
      }
    }
    for (final sectionId in region.sections) {
      final section = object.presentation.section(sectionId);
      if (section == null) continue;
      if (children.isNotEmpty) children.add(const SizedBox(height: 12));
      children.add(
        _navigationAnchor(
          view.id,
          section.id,
          _SectionCard(section: section),
        ),
      );
    }
    return Semantics(
      container: true,
      label: region.id,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: children.isEmpty
            ? const [
                _EmptyPublishedCard(
                  message: 'No content is published for this region.',
                ),
              ]
            : children,
      ),
    );
  }

  Widget? _buildComponent(
    BuildContext context,
    String component,
    String viewId,
  ) {
    switch (component) {
      case 'media':
        final mediaCommand = _mediaOrderCommand(viewId);
        return _MediaGalleryCard(
          key: const ValueKey('operator-component-media'),
          title: 'Local photographs',
          media: _orderedLocalMedia(),
          status: _mediaStatus(),
          canReorder: mediaCommand?.enabled == true && !_submitting,
          onMove: _moveMedia,
          saveOrderLabel: mediaCommand?.label,
          onSaveOrder: mediaCommand?.enabled == true && !_submitting
              ? () => _execute(mediaCommand!)
              : null,
        );
      case 'provider-media':
        final provider = object.providerMedia;
        return _MediaGalleryCard(
          key: const ValueKey('operator-component-provider-media'),
          title: provider.source.isEmpty
              ? 'Published provider photographs'
              : '${provider.source} photographs',
          media: provider.items,
          status: provider.status,
          count: provider.count,
        );
      case 'alerts':
        return _AlertsCard(alerts: object.presentation.alerts);
      case 'capability-summary':
        return _CapabilitySummary(commands: object.commands);
      case 'inventory-editor':
        return _buildInventoryEditor(context);
      case 'listing-editor':
        return _navigationAnchor(
          viewId,
          object.presentation.listingEditorId.isEmpty
              ? 'listing-editor'
              : object.presentation.listingEditorId,
          _buildListingEditor(context),
        );
      case 'pricing-context':
        final pricingTarget = object.presentation.pricingContext.id;
        final pricing = _buildPricingContext(context, viewId);
        return pricingTarget.isEmpty
            ? pricing
            : _navigationAnchor(viewId, pricingTarget, pricing);
      case 'data-navigation':
        return _DataNavigation(
          entries: object.presentation.dataNavigation,
          onNavigate: (target) => _navigateTo(viewId, target),
        );
      case 'commands':
        return _CommandPanel(
          commands: object.commands
              .where(
                (command) =>
                    command.views.isEmpty || command.views.contains(viewId),
              )
              .toList(growable: false),
          menus: object.presentation.actionMenus
              .where(
                (menu) => menu.views.isEmpty || menu.views.contains(viewId),
              )
              .toList(growable: false),
          submitting: _submitting,
          onExecute: _execute,
        );
      default:
        return _EmptyPublishedCard(
          message: 'Unsupported published component: $component',
        );
    }
  }

  Widget _navigationAnchor(String viewId, String target, Widget child) {
    if (target.isEmpty) return child;
    final key = _navigationTargets.putIfAbsent(
      '$viewId:$target',
      GlobalKey.new,
    );
    return KeyedSubtree(key: key, child: child);
  }

  Future<void> _navigateTo(String viewId, String target) async {
    final context = _navigationTargets['$viewId:$target']?.currentContext;
    if (context == null) {
      ScaffoldMessenger.of(this.context).showSnackBar(
        SnackBar(content: Text('Published section "$target" is unavailable.')),
      );
      return;
    }
    await Scrollable.ensureVisible(
      context,
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeOut,
      alignment: 0.04,
    );
  }

  OperatorCommandDescriptor? _mediaOrderCommand(String viewId) {
    for (final command in object.commands) {
      if (command.valueSource == 'media-order' &&
          (command.views.isEmpty || command.views.contains(viewId))) {
        return command;
      }
    }
    return null;
  }

  List<OperatorMediaDescriptor> _orderedLocalMedia() {
    final byName = {for (final entry in object.media) entry.name: entry};
    final images = <OperatorMediaDescriptor>[
      for (final name in _mediaOrder)
        if (byName[name] != null) byName[name]!,
    ];
    final videos = object.media
        .where((entry) => entry.kind.toLowerCase() != 'image')
        .toList(growable: false);
    return [...images, ...videos];
  }

  String _mediaStatus() {
    final status = object.item['media_status'];
    if (status is! Map) return '';
    final state = status['state']?.toString() ?? '';
    final reason = status['reason']?.toString() ?? '';
    return reason.isEmpty ? state : '$state — $reason';
  }

  Widget _buildInventoryEditor(BuildContext context) {
    final fields = object.publishedFields.itemFields;
    return _EditorCard(
      key: const ValueKey('operator-component-inventory-editor'),
      title: 'Inventory record',
      description: 'Fields and values published for this generation.',
      children: fields.values
          .map(
            (field) =>
                _buildFieldControl(context, scope: 'item_fields', field: field),
          )
          .toList(growable: false),
    );
  }

  Widget _buildListingEditor(BuildContext context) {
    final schema = object.publishedFields;
    final fields = schema.listingFields.values
        .where((field) => field.name != 'item_specifics')
        .map(
          (field) => _buildFieldControl(
            context,
            scope: 'draft_listing',
            field: field,
            optionsOverride:
                field.name == 'category_id' && field.options.isEmpty
                    ? schema.category.options
                    : null,
            hintOverride: field.name == 'price' && field.hint.isEmpty
                ? schema.pricing.categoryHint
                : null,
          ),
        )
        .toList(growable: true);

    fields.add(
      _buildFieldControl(
        context,
        scope: 'draft_listing',
        field: _conditionField,
      ),
    );
    fields.add(
      _AspectComparisonEditor(
        aspects: _aspects,
        values: Map<String, dynamic>.from(
          (Map<String, dynamic>.from(
                _editorValues['draft_listing'] as Map? ?? const {},
              )['item_specifics'] as Map?) ??
              const {},
        ),
        onChanged: _setAspectValue,
      ),
    );

    return _EditorCard(
      key: const ValueKey('operator-component-listing-editor'),
      title: 'Listing draft',
      description:
          'Category, condition, aspects, controls, and options are server-published.',
      children: fields,
    );
  }

  Widget _buildFieldControl(
    BuildContext context, {
    required String scope,
    required OperatorFieldDescriptor field,
    List<OperatorFieldOption>? optionsOverride,
    String? hintOverride,
  }) {
    final scopeValues = Map<String, dynamic>.from(
      _editorValues[scope] as Map? ?? const {},
    );
    final value = scopeValues[field.name];
    final options = optionsOverride ?? field.options;
    final hint = hintOverride?.isNotEmpty == true ? hintOverride! : field.hint;
    final label = '${field.label}${field.required ? ' *' : ''}';
    final key = ValueKey(
      '${object.objectGeneration}:$scope:${field.name}:${value.runtimeType}',
    );

    if (field.type == 'string-map') {
      return _StringMapEditor(
        key: key,
        label: label,
        value: Map<String, dynamic>.from(value as Map? ?? const {}),
        hint: hint,
        onChanged: (mapped) => _setFieldValue(scope, field.name, mapped),
      );
    }

    if (field.control == 'category-search') {
      return _CategorySearchField(
        key: ValueKey('${object.objectGeneration}:$scope:${field.name}'),
        field: field,
        value: value,
        onChanged: (changed) => _setFieldValue(scope, field.name, changed),
        sku: object.entityId,
        currentCondition: Map<String, dynamic>.from(
          _editorValues['draft_listing'] as Map? ?? const {},
        )['condition_enum'],
        onContextChanged: _applyCategoryContext,
        onContextReadinessChanged: _setCategoryContextReady,
      );
    }

    if (field.type == 'boolean') {
      return DropdownButtonFormField<bool?>(
        key: key,
        initialValue: value is bool ? value : null,
        decoration: InputDecoration(
          labelText: label,
          helperText: _orNull(hint),
        ),
        items: const [
          DropdownMenuItem<bool?>(value: null, child: Text('Not set')),
          DropdownMenuItem<bool?>(value: true, child: Text('Enabled')),
          DropdownMenuItem<bool?>(value: false, child: Text('Disabled')),
        ],
        onChanged: (changed) => _setFieldValue(scope, field.name, changed),
      );
    }

    if (options.isNotEmpty) {
      final current = value?.toString() ?? '';
      final optionValues =
          options.map((option) => option.value.toString()).toSet();
      final hasUnpublishedCurrent =
          current.isNotEmpty && !optionValues.contains(current);
      final selectedOption = options.cast<OperatorFieldOption?>().firstWhere(
            (option) => option?.value.toString() == current,
            orElse: () => null,
          );
      final selectedLabel = selectedOption?.label.isNotEmpty == true
          ? selectedOption!.label
          : field.selection.label;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          DropdownButtonFormField<String>(
            key: key,
            initialValue: current.isEmpty ? null : current,
            isExpanded: true,
            decoration: InputDecoration(
              labelText: label,
              helperText: _orNull(hint),
            ),
            items: [
              if (field.nullable && !optionValues.contains(''))
                const DropdownMenuItem(value: '', child: Text('Not set')),
              if (hasUnpublishedCurrent)
                DropdownMenuItem(
                  value: current,
                  enabled: false,
                  child: Text('$current — not in published options'),
                ),
              ...options.map(
                (option) => DropdownMenuItem(
                  value: option.value.toString(),
                  child: Text(option.label),
                ),
              ),
            ],
            onChanged: (changed) => _setFieldValue(
              scope,
              field.name,
              changed ?? (field.nullable ? null : ''),
            ),
          ),
          if (current.isNotEmpty || selectedLabel.isNotEmpty)
            _SelectionConfirmation(
              label: selectedLabel,
              value: current,
              path: field.selection.path,
            ),
        ],
      );
    }

    final multiline =
        field.control == 'textarea' || (value is String && value.length > 100);
    return TextFormField(
      key: key,
      initialValue: value?.toString() ?? '',
      keyboardType: field.type == 'number' || field.type == 'integer'
          ? const TextInputType.numberWithOptions(decimal: true, signed: true)
          : TextInputType.text,
      minLines: multiline ? 3 : 1,
      maxLines: multiline ? 8 : 1,
      decoration: InputDecoration(
        labelText: label,
        helperText: _orNull(hint),
      ),
      onChanged: (changed) =>
          _setFieldValue(scope, field.name, _typedFieldValue(field, changed)),
    );
  }

  Widget _buildPricingContext(BuildContext context, String viewId) {
    final presentation = object.presentation.pricingContext;
    final pricing = object.publishedFields.pricing;
    final rows = presentation.rows.toList(growable: true);
    if (rows.isEmpty) {
      if (_hasValue(pricing.current)) {
        rows.add(
          OperatorPresentationFact(
            id: 'current',
            label: 'Current',
            value: pricing.current,
            format: 'money',
            tone: 'neutral',
          ),
        );
      }
      if (_hasValue(pricing.target)) {
        rows.add(
          OperatorPresentationFact(
            id: 'target',
            label: 'Target',
            value: pricing.target,
            format: 'money',
            tone: 'accent',
          ),
        );
      }
      if (pricing.source.isNotEmpty) {
        rows.add(
          OperatorPresentationFact(
            id: 'source',
            label: 'Source',
            value: pricing.source,
            format: 'text',
            tone: 'neutral',
          ),
        );
      }
      if (pricing.pricedAt.isNotEmpty) {
        rows.add(
          OperatorPresentationFact(
            id: 'priced-at',
            label: 'Priced',
            value: pricing.pricedAt,
            format: 'datetime',
            tone: 'neutral',
          ),
        );
      }
    }

    final detailSections = presentation.detailsSections
        .map(object.presentation.section)
        .whereType<OperatorPresentationSection>()
        .toList(growable: false);
    final pricingCommand =
        object.commands.cast<OperatorCommandDescriptor?>().firstWhere(
              (command) => command?.id == presentation.commandId,
              orElse: () => null,
            );
    return Card(
      key: const ValueKey('operator-component-pricing-context'),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              presentation.title,
              style: Theme.of(context).textTheme.titleMedium,
            ),
            if (pricing.categoryHint.isNotEmpty) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(pricing.categoryHint),
              ),
            ],
            if (presentation.commandId.isNotEmpty ||
                presentation.searchTerms.isNotEmpty ||
                presentation.researchLinks.isNotEmpty) ...[
              const SizedBox(height: 10),
              TextFormField(
                key: ValueKey(
                  '${object.objectGeneration}:pricing-search-terms',
                ),
                initialValue: _pricingSearchTerms,
                decoration: InputDecoration(
                  labelText: 'Pricing search terms',
                  helperText: presentation.searchTermsSource.isEmpty
                      ? null
                      : 'Published source: ${presentation.searchTermsSource}',
                ),
                onChanged: (value) => _pricingSearchTerms = value,
              ),
              if (presentation.requestedSearchTerms.isNotEmpty ||
                  presentation.lastSuccessfulSearchTerms.isNotEmpty) ...[
                const SizedBox(height: 6),
                Wrap(
                  spacing: 12,
                  runSpacing: 4,
                  children: [
                    if (presentation.requestedSearchTerms.isNotEmpty)
                      Text(
                        'Requested: ${presentation.requestedSearchTerms}',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    if (presentation.lastSuccessfulSearchTerms.isNotEmpty)
                      Text(
                        'Last successful: ${presentation.lastSuccessfulSearchTerms}',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                  ],
                ),
              ],
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  if (pricingCommand != null)
                    FilledButton.icon(
                      onPressed: pricingCommand.enabled && !_submitting
                          ? () => _execute(pricingCommand)
                          : null,
                      icon: const Icon(Icons.auto_graph),
                      label: Text(pricingCommand.label),
                    ),
                  ...presentation.researchLinks.map(
                    (link) => OutlinedButton.icon(
                      onPressed: link.href.isEmpty
                          ? null
                          : () => _openResearchLink(link),
                      icon: Icon(
                        link.external ? Icons.open_in_new : Icons.link,
                        size: 17,
                      ),
                      label: Text(link.label),
                    ),
                  ),
                ],
              ),
              if (presentation.researchNote.isNotEmpty) ...[
                const SizedBox(height: 5),
                Text(
                  presentation.researchNote,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ],
            if (rows.isNotEmpty) ...[
              const SizedBox(height: 8),
              ...rows.map(
                (row) => _PropertyRow(
                  label: row.label,
                  value: row.value,
                  format: row.format,
                ),
              ),
            ],
            if (pricing.comps.isNotEmpty && detailSections.isEmpty) ...[
              const SizedBox(height: 8),
              Text(
                '${pricing.comps.length} published comparable${pricing.comps.length == 1 ? '' : 's'}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            ...detailSections.expand(
              (section) => [
                const SizedBox(height: 12),
                _navigationAnchor(
                  viewId,
                  section.id,
                  _SectionCard(section: section),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _openResearchLink(OperatorResearchLink link) async {
    final uri = Uri.tryParse(link.href);
    if (uri == null || !(uri.isScheme('http') || uri.isScheme('https'))) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Published link ${link.label} is invalid.')),
      );
      return;
    }
    final opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!opened && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not open ${link.label}.')),
      );
    }
  }
}

class _DataNavigation extends StatelessWidget {
  final List<OperatorDataNavigationEntry> entries;
  final ValueChanged<String> onNavigate;

  const _DataNavigation({required this.entries, required this.onNavigate});

  @override
  Widget build(BuildContext context) {
    final published = entries
        .where((entry) => entry.label.isNotEmpty && entry.target.isNotEmpty)
        .toList(growable: false);
    if (published.isEmpty) return const SizedBox.shrink();
    return Semantics(
      container: true,
      label: 'Published item data navigation',
      child: Wrap(
        key: const ValueKey('operator-component-data-navigation'),
        spacing: 7,
        runSpacing: 7,
        children: published
            .map(
              (entry) => ActionChip(
                key: ValueKey('operator-data-navigation-${entry.target}'),
                avatar: const Icon(Icons.arrow_downward, size: 16),
                label: Text(entry.label),
                onPressed: () => onNavigate(entry.target),
              ),
            )
            .toList(growable: false),
      ),
    );
  }
}

class _SelectionConfirmation extends StatelessWidget {
  final String label;
  final dynamic value;
  final dynamic path;

  const _SelectionConfirmation({
    required this.label,
    required this.value,
    required this.path,
  });

  @override
  Widget build(BuildContext context) {
    final id = value?.toString() ?? '';
    final display = _taxonomyPath(path, label: label);
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 5),
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 7),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(7),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            display.isEmpty ? 'Published name unavailable' : display,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.labelMedium,
          ),
          Text(
            id.isEmpty ? 'No ID selected' : 'ID $id',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _CategorySearchField extends ConsumerStatefulWidget {
  final OperatorFieldDescriptor field;
  final dynamic value;
  final ValueChanged<dynamic> onChanged;
  final String sku;
  final dynamic currentCondition;
  final ValueChanged<Map<String, dynamic>> onContextChanged;
  final ValueChanged<bool> onContextReadinessChanged;

  const _CategorySearchField({
    super.key,
    required this.field,
    required this.value,
    required this.onChanged,
    required this.sku,
    required this.currentCondition,
    required this.onContextChanged,
    required this.onContextReadinessChanged,
  });

  @override
  ConsumerState<_CategorySearchField> createState() =>
      _CategorySearchFieldState();
}

class _CategorySearchFieldState extends ConsumerState<_CategorySearchField> {
  final TextEditingController _queryController = TextEditingController();
  List<Map<String, dynamic>> _results = const [];
  List<Map<String, dynamic>> _browseStack = const [];
  late String _selectedValue;
  late String _selectedLabel;
  late dynamic _selectedPath;
  String _status = '';
  bool _loading = false;
  bool _browsing = false;
  int _requestSerial = 0;

  @override
  void initState() {
    super.initState();
    _resetSelection();
  }

  @override
  void didUpdateWidget(covariant _CategorySearchField oldWidget) {
    super.didUpdateWidget(oldWidget);
    final nextValue = widget.value?.toString() ?? '';
    if (nextValue != _selectedValue &&
        nextValue != oldWidget.value?.toString()) {
      _resetSelection();
    }
  }

  @override
  void dispose() {
    _queryController.dispose();
    super.dispose();
  }

  void _resetSelection() {
    _selectedValue = widget.value?.toString() ?? '';
    _selectedLabel = widget.field.selection.label;
    _selectedPath = widget.field.selection.path;
  }

  Future<void> _search() async {
    final query = _queryController.text.trim();
    final minimum = _lookupMinimum(widget.field.lookup['minimum_query_length']);
    if (query.isEmpty) return;
    final numeric = RegExp(r'^\d+$').hasMatch(query);
    if (!numeric && query.length < minimum) {
      setState(() {
        _status = 'Enter at least $minimum characters.';
        _results = const [];
      });
      return;
    }
    final template = (numeric
            ? widget.field.lookup['node_endpoint']
            : widget.field.lookup['search_endpoint'])
        ?.toString();
    if (template == null || template.isEmpty) {
      setState(() => _status = 'No published category lookup is available.');
      return;
    }
    final endpoint = _fillPublishedEndpoint(template, {
      'value': query,
      'query': query,
    });
    final serial = ++_requestSerial;
    setState(() {
      _loading = true;
      _browsing = false;
      _status = 'Searching published taxonomy…';
    });
    final response =
        await ref.read(apiClientProvider).getPublishedOperatorData(endpoint);
    if (!mounted || serial != _requestSerial) return;
    final data = response.data ?? const <String, dynamic>{};
    final results = numeric
        ? (response.ok ? [data] : const <Map<String, dynamic>>[])
        : _mapValues(data['results']);
    setState(() {
      _loading = false;
      _results = results;
      _status = response.ok
          ? (results.isEmpty
              ? 'No matching categories.'
              : '${results.length} published match${results.length == 1 ? '' : 'es'}.')
          : (response.error ?? 'Category lookup failed.');
    });
  }

  Future<void> _loadBrowse(
    String parentId,
    List<Map<String, dynamic>> stack,
  ) async {
    final template = widget.field.lookup['browse_endpoint']?.toString() ?? '';
    if (template.isEmpty) {
      setState(() => _status = 'No published category browser is available.');
      return;
    }
    final endpoint = _fillPublishedEndpoint(template, {'parent_id': parentId});
    final serial = ++_requestSerial;
    setState(() {
      _loading = true;
      _browsing = true;
      _status = 'Loading published taxonomy branch…';
    });
    final response =
        await ref.read(apiClientProvider).getPublishedOperatorData(endpoint);
    if (!mounted || serial != _requestSerial) return;
    final results = _mapValues(response.data?['children']);
    setState(() {
      _loading = false;
      _results = results;
      _browseStack = List<Map<String, dynamic>>.from(stack);
      _status = response.ok
          ? (results.isEmpty
              ? 'No subcategories in this branch.'
              : '${results.length} published categor${results.length == 1 ? 'y' : 'ies'}.')
          : (response.error ?? 'Category browse failed.');
    });
  }

  Future<void> _refreshCategoryContext(
    String categoryId,
    String label,
    dynamic path,
  ) async {
    final template = widget.field.lookup['context_endpoint']?.toString() ?? '';
    if (template.isEmpty) return;
    final endpoint = _fillPublishedEndpoint(template, {
      'value': categoryId,
      'id': categoryId,
      'category_id': categoryId,
      'parent_id': categoryId,
      'current_condition': widget.currentCondition?.toString() ?? '',
      'sku': widget.sku,
    });
    final serial = ++_requestSerial;
    widget.onContextReadinessChanged(false);
    setState(() {
      _loading = true;
      _status = 'Refreshing published category context…';
    });
    final response =
        await ref.read(apiClientProvider).getPublishedOperatorData(endpoint);
    if (!mounted || serial != _requestSerial) return;
    final data = response.data ?? const <String, dynamic>{};
    final context = data['context'] is Map
        ? Map<String, dynamic>.from(data['context'] as Map)
        : data;
    if (!response.ok) {
      setState(() {
        _loading = false;
        _status = response.error ?? 'Category context refresh failed.';
      });
      return;
    }

    widget.onContextChanged(context);
    final contextSelection = context['selection'] is Map
        ? Map<String, dynamic>.from(context['selection'] as Map)
        : const <String, dynamic>{};
    final contextLabel = (contextSelection['label'] ??
            context['category_name'] ??
            context['name'] ??
            label)
        .toString();
    final contextPath = contextSelection['path'] ?? path;
    final aspectsError = context['aspects_error']?.toString() ?? '';
    final aspectsCurrent = context['aspects'] is List && aspectsError.isEmpty;
    widget.onContextReadinessChanged(aspectsCurrent);
    setState(() {
      _loading = false;
      _selectedLabel = contextLabel;
      _selectedPath = contextPath;
      _status = aspectsCurrent
          ? '${contextLabel.isEmpty ? 'Category' : contextLabel} (ID $categoryId) context current.'
          : aspectsError.isNotEmpty
              ? 'Category context unavailable: $aspectsError'
              : 'Category context returned no item-specific controls.';
    });
  }

  Future<void> _openResult(Map<String, dynamic> result) async {
    final isLeaf = result['leaf'] != false && result['leaf']?.toString() != '0';
    final id = _categoryId(result);
    if (!isLeaf &&
        id.isNotEmpty &&
        (widget.field.lookup['browse_endpoint']?.toString().isNotEmpty ==
            true)) {
      await _loadBrowse(id, [..._browseStack, result]);
      return;
    }
    await _select(result);
  }

  Future<void> _select(Map<String, dynamic> result) async {
    final id = _categoryId(result);
    if (id.isEmpty) return;
    final label = _categoryLabel(result);
    final path = result['path'] ?? result['category_path'] ?? '';
    setState(() {
      _selectedValue = id;
      _selectedLabel = label;
      _selectedPath = path;
      _results = const [];
      _browseStack = const [];
      _browsing = false;
      _status = label.isEmpty
          ? 'Selected category ID $id.'
          : 'Selected $label (ID $id).';
      _queryController.clear();
    });
    widget.onChanged(id);
    await _refreshCategoryContext(id, label, path);
  }

  void _clear() {
    setState(() {
      _selectedValue = '';
      _selectedLabel = '';
      _selectedPath = '';
      _results = const [];
      _browseStack = const [];
      _status = 'No category selected.';
    });
    widget.onChanged(widget.field.nullable ? null : '');
  }

  @override
  Widget build(BuildContext context) {
    final label = '${widget.field.label}${widget.field.required ? ' *' : ''}';
    final browsePath = _browseStack
        .map(_categoryLabel)
        .where((name) => name.isNotEmpty)
        .join(' › ');
    return DecoratedBox(
      decoration: BoxDecoration(
        border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(label, style: Theme.of(context).textTheme.labelLarge),
            if (_selectedValue.isNotEmpty || _selectedLabel.isNotEmpty) ...[
              _SelectionConfirmation(
                label: _selectedLabel,
                value: _selectedValue,
                path: _selectedPath,
              ),
              const SizedBox(height: 8),
            ],
            TextField(
              controller: _queryController,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                labelText: 'Search by category name or ID',
                helperText: _orNull(widget.field.hint),
                suffixIcon: IconButton(
                  tooltip: 'Search categories',
                  onPressed: _loading ? null : _search,
                  icon: const Icon(Icons.manage_search),
                ),
              ),
              onSubmitted: (_) => _loading ? null : _search(),
            ),
            const SizedBox(height: 7),
            Wrap(
              spacing: 7,
              runSpacing: 7,
              children: [
                if (widget.field.lookup['browse_endpoint']
                        ?.toString()
                        .isNotEmpty ==
                    true)
                  OutlinedButton.icon(
                    onPressed:
                        _loading ? null : () => _loadBrowse('', const []),
                    icon: const Icon(Icons.account_tree_outlined),
                    label: const Text('Browse'),
                  ),
                if (widget.field.nullable && _selectedValue.isNotEmpty)
                  TextButton.icon(
                    onPressed: _loading ? null : _clear,
                    icon: const Icon(Icons.clear),
                    label: const Text('Clear'),
                  ),
              ],
            ),
            if (_loading) const LinearProgressIndicator(),
            if (_browsing) ...[
              const SizedBox(height: 7),
              Row(
                children: [
                  IconButton(
                    tooltip: 'Back one category level',
                    onPressed: _loading || _browseStack.isEmpty
                        ? null
                        : () {
                            final stack = List<Map<String, dynamic>>.from(
                              _browseStack,
                            )..removeLast();
                            final parentId =
                                stack.isEmpty ? '' : _categoryId(stack.last);
                            _loadBrowse(parentId, stack);
                          },
                    icon: const Icon(Icons.arrow_back),
                  ),
                  Expanded(
                    child: Text(
                      browsePath.isEmpty ? 'All categories' : browsePath,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ],
            if (_results.isNotEmpty)
              ConstrainedBox(
                constraints: const BoxConstraints(maxHeight: 270),
                child: ListView.builder(
                  shrinkWrap: true,
                  itemCount: _results.length,
                  itemBuilder: (context, index) {
                    final result = _results[index];
                    final id = _categoryId(result);
                    final name = _categoryLabel(result);
                    final path = _taxonomyPath(
                      result['path'] ?? result['category_path'],
                      label: name,
                    );
                    final isLeaf = result['leaf'] != false &&
                        result['leaf']?.toString() != '0';
                    return ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text(path.isEmpty ? name : path),
                      subtitle: Text(id.isEmpty ? 'No ID published' : 'ID $id'),
                      trailing: Icon(
                        !isLeaf && _browsing
                            ? Icons.chevron_right
                            : Icons.check_circle_outline,
                      ),
                      onTap: id.isEmpty ? null : () => _openResult(result),
                    );
                  },
                ),
              ),
            if (_status.isNotEmpty) ...[
              const SizedBox(height: 5),
              Text(_status, style: Theme.of(context).textTheme.bodySmall),
            ],
          ],
        ),
      ),
    );
  }
}

class _EditorCard extends StatelessWidget {
  final String title;
  final String description;
  final List<Widget> children;

  const _EditorCard({
    super.key,
    required this.title,
    required this.description,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 3),
            Text(
              description,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.outline,
                  ),
            ),
            const SizedBox(height: 12),
            LayoutBuilder(
              builder: (context, constraints) {
                final columns = constraints.maxWidth >= 680;
                if (!columns) {
                  return Column(
                    children: [
                      for (var index = 0; index < children.length; index++) ...[
                        if (index > 0) const SizedBox(height: 12),
                        children[index],
                      ],
                    ],
                  );
                }
                return Wrap(
                  spacing: 14,
                  runSpacing: 14,
                  children: children
                      .map(
                        (child) => SizedBox(
                          width: (constraints.maxWidth - 14) / 2,
                          child: child,
                        ),
                      )
                      .toList(growable: false),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _StringMapEditor extends StatefulWidget {
  final String label;
  final Map<String, dynamic> value;
  final String hint;
  final ValueChanged<Map<String, dynamic>> onChanged;

  const _StringMapEditor({
    super.key,
    required this.label,
    required this.value,
    required this.hint,
    required this.onChanged,
  });

  @override
  State<_StringMapEditor> createState() => _StringMapEditorState();
}

class _StringMapEditorState extends State<_StringMapEditor> {
  late Map<String, dynamic> _values;

  @override
  void initState() {
    super.initState();
    _values = Map<String, dynamic>.from(widget.value);
  }

  Future<void> _addEntry() async {
    final nameController = TextEditingController();
    final valueController = TextEditingController();
    final entry = await showDialog<MapEntry<String, String>>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Add ${widget.label} field'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: nameController,
              decoration: const InputDecoration(labelText: 'Name'),
            ),
            TextField(
              controller: valueController,
              decoration: const InputDecoration(labelText: 'Value'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              final name = nameController.text.trim();
              if (name.isEmpty) return;
              Navigator.pop(
                dialogContext,
                MapEntry(name, valueController.text),
              );
            },
            child: const Text('Add'),
          ),
        ],
      ),
    );
    nameController.dispose();
    valueController.dispose();
    if (entry == null || !mounted) return;
    setState(() => _values[entry.key] = entry.value);
    widget.onChanged(Map<String, dynamic>.from(_values));
  }

  void _remove(String name) {
    setState(() => _values.remove(name));
    widget.onChanged(Map<String, dynamic>.from(_values));
  }

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(widget.label, style: Theme.of(context).textTheme.labelLarge),
            if (widget.hint.isNotEmpty) ...[
              const SizedBox(height: 2),
              Text(widget.hint, style: Theme.of(context).textTheme.bodySmall),
            ],
            const SizedBox(height: 8),
            if (_values.isEmpty)
              const Text('No published values.')
            else
              ..._values.entries.map(
                (entry) => Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 126,
                        child: Text(entry.key, overflow: TextOverflow.ellipsis),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: TextFormField(
                          key: ValueKey(entry.key),
                          initialValue: entry.value?.toString() ?? '',
                          onChanged: (value) {
                            _values[entry.key] = value;
                            widget.onChanged(
                              Map<String, dynamic>.from(_values),
                            );
                          },
                        ),
                      ),
                      IconButton(
                        tooltip: 'Remove ${entry.key}',
                        onPressed: () => _remove(entry.key),
                        icon: const Icon(Icons.remove_circle_outline),
                      ),
                    ],
                  ),
                ),
              ),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: _addEntry,
                icon: const Icon(Icons.add),
                label: const Text('Add field'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _AspectComparisonEditor extends StatelessWidget {
  final List<OperatorAspectDescriptor> aspects;
  final Map<String, dynamic> values;
  final void Function(String name, dynamic value) onChanged;

  const _AspectComparisonEditor({
    required this.aspects,
    required this.values,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Unified item specifics',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const SizedBox(height: 3),
            Text(
              'Inventory, proposal, provider, and draft values published by the API.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 10),
            if (aspects.isEmpty)
              const Text('No category aspects are published.')
            else
              ...aspects.map(
                (aspect) => Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Wrap(
                        spacing: 6,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          Text(
                            aspect.name,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                          if (aspect.required)
                            const Chip(
                              label: Text('Required'),
                              visualDensity: VisualDensity.compact,
                            ),
                          if (aspect.custom)
                            const Chip(
                              label: Text('Custom'),
                              visualDensity: VisualDensity.compact,
                            ),
                        ],
                      ),
                      const SizedBox(height: 5),
                      Wrap(
                        spacing: 8,
                        runSpacing: 5,
                        children: [
                          _ComparisonValue(
                            label: 'Inventory',
                            value: aspect.inventoryValue,
                          ),
                          _ComparisonValue(
                            label: 'Proposed',
                            value: aspect.proposedValue,
                          ),
                          _ComparisonValue(
                            label: 'Provider',
                            value: aspect.liveValue,
                          ),
                        ],
                      ),
                      const SizedBox(height: 7),
                      if (aspect.allowedValues.isNotEmpty)
                        DropdownButtonFormField<String>(
                          key: ValueKey('aspect-${aspect.name}'),
                          initialValue: _dropdownAspectValue(
                            values[aspect.name],
                            aspect.allowedValues,
                          ),
                          isExpanded: true,
                          decoration: const InputDecoration(
                            labelText: 'Draft value',
                          ),
                          items: [
                            const DropdownMenuItem(
                              value: '',
                              child: Text('Not set'),
                            ),
                            ...aspect.allowedValues.map(
                              (value) => DropdownMenuItem(
                                value: value,
                                child: Text(value),
                              ),
                            ),
                          ],
                          onChanged: (value) =>
                              onChanged(aspect.name, value ?? ''),
                        )
                      else
                        TextFormField(
                          key: ValueKey('aspect-${aspect.name}'),
                          initialValue: values[aspect.name]?.toString() ?? '',
                          decoration: const InputDecoration(
                            labelText: 'Draft value',
                          ),
                          onChanged: (value) => onChanged(aspect.name, value),
                        ),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _ComparisonValue extends StatelessWidget {
  final String label;
  final dynamic value;

  const _ComparisonValue({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text('$label: ${_formatText(value, 'text')}'),
    );
  }
}

class _MediaGalleryCard extends StatelessWidget {
  final String title;
  final List<OperatorMediaDescriptor> media;
  final String status;
  final int? count;
  final bool canReorder;
  final void Function(int from, int to)? onMove;
  final String? saveOrderLabel;
  final VoidCallback? onSaveOrder;

  const _MediaGalleryCard({
    super.key,
    required this.title,
    required this.media,
    required this.status,
    this.count,
    this.canReorder = false,
    this.onMove,
    this.saveOrderLabel,
    this.onSaveOrder,
  });

  @override
  Widget build(BuildContext context) {
    final images = media
        .where((entry) => entry.kind.toLowerCase() == 'image')
        .toList(growable: false);
    final otherMedia = media
        .where((entry) => entry.kind.toLowerCase() != 'image')
        .toList(growable: false);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    title,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Text('${count ?? images.length}'),
              ],
            ),
            if (status.isNotEmpty) ...[
              const SizedBox(height: 2),
              Text(status, style: Theme.of(context).textTheme.bodySmall),
            ],
            const SizedBox(height: 10),
            if (images.isEmpty)
              const _EmptyInline(message: 'No photographs are published.')
            else
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (var index = 0; index < images.length; index++)
                    SizedBox(
                      width: 154,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          ClipRRect(
                            borderRadius: BorderRadius.circular(7),
                            child: _PublishedImage(
                              media: images[index],
                              width: 154,
                              height: 116,
                            ),
                          ),
                          const SizedBox(height: 3),
                          Text(
                            images[index].name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.labelSmall,
                          ),
                          if (canReorder)
                            Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                IconButton(
                                  visualDensity: VisualDensity.compact,
                                  tooltip: 'Move earlier',
                                  onPressed: index == 0
                                      ? null
                                      : () => onMove?.call(index, index - 1),
                                  icon: const Icon(Icons.arrow_back, size: 18),
                                ),
                                IconButton(
                                  visualDensity: VisualDensity.compact,
                                  tooltip: 'Move later',
                                  onPressed: index == images.length - 1
                                      ? null
                                      : () => onMove?.call(index, index + 1),
                                  icon: const Icon(
                                    Icons.arrow_forward,
                                    size: 18,
                                  ),
                                ),
                              ],
                            ),
                        ],
                      ),
                    ),
                ],
              ),
            if (otherMedia.isNotEmpty) ...[
              const SizedBox(height: 10),
              ...otherMedia.map(
                (entry) => ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.movie_outlined),
                  title: Text(entry.name),
                  subtitle: Text(entry.kind),
                ),
              ),
            ],
            if (saveOrderLabel?.isNotEmpty == true) ...[
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerLeft,
                child: FilledButton.tonalIcon(
                  onPressed: onSaveOrder,
                  icon: const Icon(Icons.save_outlined),
                  label: Text(saveOrderLabel!),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _PublishedImage extends ConsumerWidget {
  final OperatorMediaDescriptor media;
  final double width;
  final double height;

  const _PublishedImage({
    required this.media,
    required this.width,
    required this.height,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final api = ref.read(apiClientProvider);
    final parsed = Uri.tryParse(media.url);
    final isRelative = parsed == null || !parsed.hasScheme;
    final url = isRelative ? api.mediaUrl(media.url) : media.url;
    return CachedNetworkImage(
      imageUrl: url,
      httpHeaders: isRelative ? api.authHeaders : const {},
      width: width,
      height: height,
      fit: BoxFit.cover,
      placeholder: (_, __) => const Center(child: CircularProgressIndicator()),
      errorWidget: (_, __, ___) => ColoredBox(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        child: const Center(child: Icon(Icons.broken_image_outlined)),
      ),
    );
  }
}

class _AlertsCard extends StatelessWidget {
  final List<Map<String, dynamic>> alerts;

  const _AlertsCard({required this.alerts});

  @override
  Widget build(BuildContext context) {
    if (alerts.isEmpty) return const SizedBox.shrink();
    return Card(
      key: const ValueKey('operator-component-alerts'),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Attention', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            ...alerts.map((alert) {
              final level = (alert['level'] ?? 'info').toString();
              final details = (alert['details'] as List? ?? const [])
                  .whereType<Map>()
                  .map((row) => Map<String, dynamic>.from(row));
              return Container(
                width: double.infinity,
                margin: const EdgeInsets.only(bottom: 8),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  border: Border(
                    left: BorderSide(
                      color: _toneColor(context, level),
                      width: 4,
                    ),
                  ),
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      (alert['title'] ?? level).toString(),
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    if (_hasValue(alert['message']))
                      Text('${alert['message']}'),
                    ...details.map(
                      (row) => _PropertyRow(
                        label: (row['label'] ?? '').toString(),
                        value: row['value'],
                        format: (row['format'] ?? 'text').toString(),
                      ),
                    ),
                  ],
                ),
              );
            }),
          ],
        ),
      ),
    );
  }
}

class _CapabilitySummary extends StatelessWidget {
  final List<OperatorCommandDescriptor> commands;

  const _CapabilitySummary({required this.commands});

  @override
  Widget build(BuildContext context) {
    return Card(
      key: const ValueKey('operator-component-capability-summary'),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Listing capabilities',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (commands.isEmpty)
              const _EmptyInline(message: 'No commands are published.')
            else
              ...commands.map(
                (command) => ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text(command.label),
                  subtitle: Text(
                    command.reason ?? command.authorityScope,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  trailing: Chip(
                    label: Text(command.enabled ? 'Available' : 'Held'),
                    side: BorderSide(
                      color: command.enabled
                          ? Theme.of(context).colorScheme.primary
                          : Theme.of(context).colorScheme.outline,
                    ),
                    visualDensity: VisualDensity.compact,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _CommandPanel extends StatefulWidget {
  final List<OperatorCommandDescriptor> commands;
  final List<OperatorActionMenuDescriptor> menus;
  final bool submitting;
  final Future<void> Function(OperatorCommandDescriptor command) onExecute;

  const _CommandPanel({
    required this.commands,
    required this.menus,
    required this.submitting,
    required this.onExecute,
  });

  @override
  State<_CommandPanel> createState() => _CommandPanelState();
}

class _CommandPanelState extends State<_CommandPanel> {
  final Map<String, String> _selectedCommands = {};

  List<OperatorCommandDescriptor> _commandsFor(
    OperatorActionMenuDescriptor menu,
  ) {
    final byId = {for (final command in widget.commands) command.id: command};
    return menu.commandIds
        .map((id) => byId[id])
        .whereType<OperatorCommandDescriptor>()
        .toList(growable: false);
  }

  OperatorCommandDescriptor? _selectedFor(
    OperatorActionMenuDescriptor menu,
    List<OperatorCommandDescriptor> commands,
  ) {
    if (commands.isEmpty) return null;
    final selectedId = _selectedCommands[menu.id] ?? menu.defaultCommandId;
    for (final command in commands) {
      if (command.id == selectedId) return command;
    }
    return commands.first;
  }

  @override
  Widget build(BuildContext context) {
    final menus = widget.menus
        .map((menu) => MapEntry(menu, _commandsFor(menu)))
        .where((entry) => entry.value.isNotEmpty)
        .toList(growable: false);
    return Card(
      key: const ValueKey('operator-component-commands'),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Published commands',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (menus.isEmpty)
              const _EmptyInline(
                message: 'No action menus are published for this view.',
              )
            else
              ...menus.map(
                (entry) {
                  final menu = entry.key;
                  final commands = entry.value;
                  final selected = _selectedFor(menu, commands)!;
                  final reason = selected.reason ??
                      (selected.enabled ? 'Available now' : 'Held');
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                          menu.label,
                          style: Theme.of(context).textTheme.labelLarge,
                        ),
                        const SizedBox(height: 5),
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Expanded(
                              child: DropdownButtonFormField<String>(
                                key: ValueKey(
                                  'operator-action-menu-${menu.id}-${selected.id}',
                                ),
                                initialValue: selected.id,
                                isExpanded: true,
                                decoration: const InputDecoration(
                                  labelText: 'Action',
                                  isDense: true,
                                ),
                                items: commands
                                    .map(
                                      (command) => DropdownMenuItem(
                                        value: command.id,
                                        child: Text(command.label),
                                      ),
                                    )
                                    .toList(growable: false),
                                onChanged: widget.submitting
                                    ? null
                                    : (commandId) {
                                        if (commandId == null) return;
                                        setState(
                                          () => _selectedCommands[menu.id] =
                                              commandId,
                                        );
                                      },
                              ),
                            ),
                            const SizedBox(width: 8),
                            _CommandButton(
                              command: selected,
                              label: 'Execute',
                              onPressed: selected.enabled && !widget.submitting
                                  ? () => widget.onExecute(selected)
                                  : null,
                            ),
                          ],
                        ),
                        const SizedBox(height: 3),
                        Text(
                          reason,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],
                    ),
                  );
                },
              ),
          ],
        ),
      ),
    );
  }
}

class _CommandButton extends StatelessWidget {
  final OperatorCommandDescriptor command;
  final String? label;
  final VoidCallback? onPressed;

  const _CommandButton({
    required this.command,
    this.label,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    final tone = command.tone.toLowerCase();
    if (tone == 'danger' || tone == 'error' || tone == 'destructive') {
      return FilledButton(
        style: FilledButton.styleFrom(
          backgroundColor: Theme.of(context).colorScheme.error,
          foregroundColor: Theme.of(context).colorScheme.onError,
        ),
        onPressed: onPressed,
        child: Text(label ?? command.label),
      );
    }
    if (tone == 'primary' || tone == 'success') {
      return FilledButton(
        onPressed: onPressed,
        child: Text(label ?? command.label),
      );
    }
    return FilledButton.tonal(
      onPressed: onPressed,
      child: Text(label ?? command.label),
    );
  }
}

class _SectionCard extends StatelessWidget {
  final OperatorPresentationSection section;

  const _SectionCard({required this.section});

  @override
  Widget build(BuildContext context) {
    final content = _sectionContent(context);
    if (section.collapsed) {
      return Card(
        key: ValueKey('operator-section-${section.id}'),
        clipBehavior: Clip.antiAlias,
        child: ExpansionTile(
          title: Text(section.title),
          subtitle:
              section.description.isEmpty ? null : Text(section.description),
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
              child: content,
            ),
          ],
        ),
      );
    }
    return Card(
      key: ValueKey('operator-section-${section.id}'),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(section.title, style: Theme.of(context).textTheme.titleMedium),
            if (section.description.isNotEmpty) ...[
              const SizedBox(height: 3),
              Text(
                section.description,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            const SizedBox(height: 8),
            content,
          ],
        ),
      ),
    );
  }

  Widget _sectionContent(BuildContext context) {
    switch (section.kind) {
      case 'table':
        return _PublishedTable(columns: section.columns, rows: section.rows);
      case 'tree':
        return _StructuredValue(value: section.value);
      default:
        if (section.rows.isEmpty) {
          return const _EmptyInline(message: 'No values are published.');
        }
        return Column(
          children: section.rows
              .map(
                (row) => _PropertyRow(
                  label: (row['label'] ?? '').toString(),
                  value: row['value'],
                  format: (row['format'] ?? 'text').toString(),
                ),
              )
              .toList(growable: false),
        );
    }
  }
}

class _PublishedTable extends StatelessWidget {
  final List<Map<String, dynamic>> columns;
  final List<Map<String, dynamic>> rows;

  const _PublishedTable({required this.columns, required this.rows});

  @override
  Widget build(BuildContext context) {
    if (columns.isEmpty || rows.isEmpty) {
      return const _EmptyInline(message: 'No table rows are published.');
    }
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: DataTable(
        columns: columns
            .map(
              (column) => DataColumn(
                label: Text(
                  (column['label'] ?? column['key'] ?? '').toString(),
                ),
              ),
            )
            .toList(growable: false),
        rows: rows
            .map(
              (row) => DataRow(
                cells: columns
                    .map(
                      (column) => DataCell(
                        ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 280),
                          child: _StructuredValue(
                            value: row[column['key']],
                            format: (column['format'] ?? 'text').toString(),
                          ),
                        ),
                      ),
                    )
                    .toList(growable: false),
              ),
            )
            .toList(growable: false),
      ),
    );
  }
}

class _PropertyRow extends StatelessWidget {
  final String label;
  final dynamic value;
  final String format;

  const _PropertyRow({
    required this.label,
    required this.value,
    this.format = 'text',
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 132,
            child: Text(
              label,
              style: TextStyle(color: Theme.of(context).colorScheme.outline),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: _StructuredValue(value: value, format: format),
          ),
        ],
      ),
    );
  }
}

class _StructuredValue extends StatelessWidget {
  final dynamic value;
  final String format;

  const _StructuredValue({required this.value, this.format = 'text'});

  @override
  Widget build(BuildContext context) {
    if (value is Map) {
      final entries = (value as Map).entries.toList(growable: false);
      if (entries.isEmpty) return const Text('None');
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: entries
            .map(
              (entry) => ExpansionTile(
                dense: true,
                tilePadding: EdgeInsets.zero,
                childrenPadding: const EdgeInsets.only(left: 12, bottom: 6),
                title: Text(entry.key.toString()),
                children: [_StructuredValue(value: entry.value)],
              ),
            )
            .toList(growable: false),
      );
    }
    if (value is List) {
      if ((value as List).isEmpty) return const Text('None');
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: (value as List)
            .asMap()
            .entries
            .map(
              (entry) => entry.value is Map || entry.value is List
                  ? ExpansionTile(
                      dense: true,
                      tilePadding: EdgeInsets.zero,
                      title: Text('Entry ${entry.key + 1}'),
                      children: [_StructuredValue(value: entry.value)],
                    )
                  : Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Text('• ${_formatText(entry.value, format)}'),
                    ),
            )
            .toList(growable: false),
      );
    }
    return Text(
      _formatText(value, format),
      style: format == 'code' ? const TextStyle(fontFamily: 'monospace') : null,
    );
  }
}

class _EmptyPublishedCard extends StatelessWidget {
  final String message;

  const _EmptyPublishedCard({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Card(
        margin: const EdgeInsets.all(16),
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Text(message, textAlign: TextAlign.center),
        ),
      ),
    );
  }
}

class _EmptyInline extends StatelessWidget {
  final String message;

  const _EmptyInline({required this.message});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
        borderRadius: BorderRadius.circular(7),
      ),
      child: Text(message, textAlign: TextAlign.center),
    );
  }
}

String _shortGeneration(String generation) {
  return generation.length <= 12 ? generation : generation.substring(0, 12);
}

bool _hasValue(dynamic value) {
  return value != null && value != '' && value != const [] && value != const {};
}

bool _hasAnyAspectValue(
  OperatorAspectDescriptor aspect,
  dynamic editorValue,
) {
  return [
    editorValue,
    aspect.value,
    aspect.inventoryValue,
    aspect.liveValue,
    aspect.proposedValue,
  ].any(_hasValue);
}

String? _orNull(String value) => value.isEmpty ? null : value;

dynamic _typedFieldValue(OperatorFieldDescriptor field, String value) {
  if (value.isEmpty && field.nullable) return null;
  switch (field.type) {
    case 'number':
      return num.tryParse(value) ?? value;
    case 'integer':
      return int.tryParse(value) ?? value;
    default:
      return value;
  }
}

String? _dropdownAspectValue(dynamic value, List<String> allowedValues) {
  final selected = value?.toString() ?? '';
  if (selected.isEmpty) return '';
  return allowedValues.contains(selected) ? selected : null;
}

int _lookupMinimum(dynamic value) {
  if (value is num) return value.toInt().clamp(1, 100);
  return int.tryParse(value?.toString() ?? '')?.clamp(1, 100) ?? 2;
}

String _fillPublishedEndpoint(
  String template,
  Map<String, String> values,
) {
  var result = template;
  for (final entry in values.entries) {
    result = result.replaceAll(
      '{${entry.key}}',
      Uri.encodeQueryComponent(entry.value),
    );
  }
  return result;
}

List<Map<String, dynamic>> _mapValues(dynamic value) {
  if (value is! List) return const [];
  return value
      .whereType<Map>()
      .map((entry) => Map<String, dynamic>.from(entry))
      .toList(growable: false);
}

String _categoryId(Map<String, dynamic> category) {
  return (category['id'] ?? category['category_id'] ?? category['value'] ?? '')
      .toString();
}

String _categoryLabel(Map<String, dynamic> category) {
  return (category['name'] ??
          category['category_name'] ??
          category['label'] ??
          '')
      .toString();
}

String _taxonomyPath(dynamic value, {String label = ''}) {
  final parts = <String>[];

  void append(dynamic part) {
    if (part is List) {
      for (final entry in part) {
        append(entry);
      }
      return;
    }
    if (part is Map) {
      append(
        part['label'] ?? part['name'] ?? part['title'] ?? part['value'] ?? '',
      );
      return;
    }
    for (final segment in (part?.toString() ?? '').split(
      RegExp(r'\s*(?:›|>)\s*'),
    )) {
      final trimmed = segment.trim();
      if (trimmed.isNotEmpty) parts.add(trimmed);
    }
  }

  append(value);
  final leaf = label.trim();
  if (leaf.isNotEmpty &&
      (parts.isEmpty || parts.last.toLowerCase() != leaf.toLowerCase())) {
    parts.add(leaf);
  }
  return parts.join(' › ');
}

String _formatText(dynamic value, String format) {
  if (!_hasValue(value)) return '—';
  if (format == 'money' && value is num) {
    return NumberFormat.simpleCurrency(name: 'USD').format(value);
  }
  if (format == 'datetime') {
    final parsed = DateTime.tryParse(value.toString());
    if (parsed != null) return DateFormat.yMMMd().add_jm().format(parsed);
  }
  if (format == 'boolean' && value is bool) return value ? 'Yes' : 'No';
  if (value is List) return value.map((entry) => entry.toString()).join(', ');
  if (value is Map) return '${value.length} published fields';
  return value.toString();
}

Color _toneColor(BuildContext context, String tone) {
  switch (tone.toLowerCase()) {
    case 'error':
    case 'danger':
    case 'destructive':
      return Theme.of(context).colorScheme.error;
    case 'warning':
      return Colors.orange;
    case 'success':
      return Colors.green;
    case 'accent':
    case 'primary':
      return Theme.of(context).colorScheme.primary;
    default:
      return Theme.of(context).colorScheme.outlineVariant;
  }
}
