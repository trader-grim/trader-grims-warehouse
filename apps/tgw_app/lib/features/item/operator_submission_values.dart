import '../../models/models.dart';

class UnsupportedOperatorValueSource implements Exception {
  final String valueSource;

  const UnsupportedOperatorValueSource(this.valueSource);

  @override
  String toString() => 'unsupported operator value source: $valueSource';
}

Map<String, dynamic> initialOperatorEditorValues(OperatorObjectView object) {
  final itemFields = <String, dynamic>{
    for (final entry in object.publishedFields.itemFields.entries)
      entry.key: _copyValue(entry.value.value),
  };
  final listingFields = <String, dynamic>{
    for (final entry in object.publishedFields.listingFields.entries)
      entry.key: _copyValue(entry.value.value),
  };

  final specifics = _mapping(listingFields['item_specifics']);
  for (final aspect in object.publishedFields.aspects) {
    if (aspect.name.isNotEmpty) {
      if (aspect.value != null) {
        specifics[aspect.name] = _copyValue(aspect.value);
      } else {
        specifics.putIfAbsent(aspect.name, () => '');
      }
    }
  }
  if (object.publishedFields.listingFields.containsKey('item_specifics')) {
    listingFields['item_specifics'] = specifics;
  }
  listingFields['condition_enum'] = object.publishedFields.condition.value;

  return {'item_fields': itemFields, 'draft_listing': listingFields};
}

Map<String, dynamic> buildOperatorCommandValues({
  required OperatorObjectView object,
  required OperatorCommandDescriptor command,
  required Map<String, dynamic> editorValues,
  required List<String> mediaOrder,
  String? pricingSearchTerms,
  Iterable<String> contextAspectNames = const [],
}) {
  switch (command.valueSource) {
    case 'editor':
      return _editorValues(
        object,
        command,
        editorValues,
        contextAspectNames,
      );
    case 'workflow':
      return _workflowValues(
        object,
        command.inputSchema,
        editorValues,
        contextAspectNames,
      );
    case 'none':
      return <String, dynamic>{};
    case 'media-order':
      final properties = _properties(command.inputSchema);
      if (!properties.containsKey('order')) return <String, dynamic>{};
      return {'order': List<String>.from(mediaOrder)};
    case 'pricing':
      final properties = _properties(command.inputSchema);
      if (!properties.containsKey('search_terms')) {
        return <String, dynamic>{};
      }
      return {
        'search_terms': pricingSearchTerms ??
            object.presentation.pricingContext.searchTerms,
      };
    default:
      throw UnsupportedOperatorValueSource(command.valueSource);
  }
}

Map<String, dynamic> _editorValues(
  OperatorObjectView object,
  OperatorCommandDescriptor command,
  Map<String, dynamic> editorValues,
  Iterable<String> contextAspectNames,
) {
  final commandProperties = _properties(command.inputSchema);
  final completeListingEditor =
      command.id == 'list-item' || command.id == 'update-item';
  final result = <String, dynamic>{};

  void addScope(
    String scope,
    Map<String, OperatorFieldDescriptor> publishedFields,
  ) {
    final scopeSchema = _mapping(commandProperties[scope]);
    final fieldProperties = _properties(scopeSchema);
    if (fieldProperties.isEmpty) return;
    final editedScope = _mapping(editorValues[scope]);
    final selected = <String, dynamic>{};
    for (final entry in fieldProperties.entries) {
      final name = entry.key;
      final propertySchema = _mapping(entry.value);
      if (scope == 'draft_listing' && name == 'condition_enum') {
        final value = editedScope[name];
        if (completeListingEditor ||
            _publishedConditionValue(object, propertySchema, value)) {
          selected[name] = value;
        }
        continue;
      }
      if (!publishedFields.containsKey(name) ||
          !editedScope.containsKey(name)) {
        continue;
      }
      final value = editedScope[name];
      if (name == 'item_specifics') {
        selected[name] = _publishedAspectValues(
          object,
          propertySchema,
          value,
          contextAspectNames: contextAspectNames,
          includeEmpty: true,
        );
        continue;
      }
      final field = publishedFields[name]!;
      final serialized = _editorFieldValue(
        field,
        propertySchema,
        value,
      );
      if (!completeListingEditor &&
          _invalidEnumValue(propertySchema, serialized)) {
        continue;
      }
      selected[name] = _copyValue(serialized);
    }
    if (selected.isNotEmpty) result[scope] = selected;
  }

  addScope('item_fields', object.publishedFields.itemFields);
  addScope('draft_listing', object.publishedFields.listingFields);
  return result;
}

Map<String, dynamic> _workflowValues(
  OperatorObjectView object,
  Map<String, dynamic> inputSchema,
  Map<String, dynamic> editorValues,
  Iterable<String> contextAspectNames,
) {
  final properties = _properties(inputSchema);
  final draft = _mapping(editorValues['draft_listing']);
  final values = <String, dynamic>{};

  final conditionSchema = _mapping(properties['condition_enum']);
  if (conditionSchema.isNotEmpty) {
    final conditionValue = draft['condition_enum'];
    if (_publishedConditionValue(object, conditionSchema, conditionValue)) {
      values['condition_enum'] = conditionValue;
    }
  }

  final aspectSchema = _mapping(properties['item_specifics']);
  if (aspectSchema.isNotEmpty) {
    final specifics = _publishedAspectValues(
      object,
      aspectSchema,
      draft['item_specifics'],
      contextAspectNames: contextAspectNames,
      includeEmpty: false,
    );
    if (specifics.isNotEmpty) values['item_specifics'] = specifics;
  }
  return values;
}

bool _publishedConditionValue(
  OperatorObjectView object,
  Map<String, dynamic> propertySchema,
  dynamic value,
) {
  final published = object.fieldSchema['condition'];
  if (published is! Map || !published.containsKey('value')) return false;
  if (value == null || (value is String && value.trim().isEmpty)) return false;
  return !_invalidEnumValue(propertySchema, value);
}

Map<String, dynamic> _publishedAspectValues(
  OperatorObjectView object,
  Map<String, dynamic> propertySchema,
  dynamic rawValues, {
  required Iterable<String> contextAspectNames,
  required bool includeEmpty,
}) {
  final values = _mapping(rawValues);
  final nestedProperties = _properties(propertySchema);
  final publishedNames = <String>{
    ...object.publishedFields.aspects
        .map((aspect) => aspect.name)
        .where((name) => name.isNotEmpty),
  };
  final listingSpecifics =
      object.publishedFields.listingFields['item_specifics']?.value;
  if (listingSpecifics is Map) {
    publishedNames.addAll(
      listingSpecifics.keys.whereType<String>().where(
            (name) => name.isNotEmpty,
          ),
    );
  }
  final contextNames =
      contextAspectNames.where((name) => name.isNotEmpty).toSet();
  publishedNames.addAll(contextNames);

  final result = <String, dynamic>{};
  for (final name in publishedNames) {
    if (nestedProperties.isNotEmpty &&
        !nestedProperties.containsKey(name) &&
        !contextNames.contains(name)) {
      continue;
    }
    final value = values[name];
    if (!includeEmpty &&
        (value == null || (value is String && value.trim().isEmpty))) {
      continue;
    }
    final serialized = value?.toString() ?? '';
    final nestedSchema = _mapping(nestedProperties[name]);
    if (_invalidEnumValue(nestedSchema, serialized)) continue;
    result[name] = serialized;
  }
  return result;
}

dynamic _editorFieldValue(
  OperatorFieldDescriptor field,
  Map<String, dynamic> propertySchema,
  dynamic value,
) {
  final selector = field.control == 'select' ||
      field.control == 'category-search' ||
      field.options.isNotEmpty ||
      propertySchema['enum'] is List;
  if ((field.nullable || propertySchema['nullable'] == true) &&
      selector &&
      (value == null || value == '')) {
    return null;
  }
  return value;
}

bool _invalidEnumValue(Map<String, dynamic> schema, dynamic value) {
  final allowedValues = schema['enum'];
  if (value == null) return schema['nullable'] != true;
  return allowedValues is List && !allowedValues.contains(value);
}

Map<String, dynamic> _properties(dynamic rawSchema) {
  final schema = _mapping(rawSchema);
  if (schema['type'] != 'object') return const {};
  return _mapping(schema['properties']);
}

Map<String, dynamic> _mapping(dynamic value) {
  return value is Map ? Map<String, dynamic>.from(value) : <String, dynamic>{};
}

dynamic _copyValue(dynamic value) {
  if (value is Map) {
    return {
      for (final entry in value.entries)
        if (entry.key is String) entry.key as String: _copyValue(entry.value),
    };
  }
  if (value is List) return value.map(_copyValue).toList();
  return value;
}
