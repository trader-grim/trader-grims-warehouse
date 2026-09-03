import '../../models/models.dart';

Map<String, dynamic> buildOperatorEditorValues(
  OperatorObjectView object,
  OperatorCommandDescriptor command,
) {
  final commandProperties = _properties(command.inputSchema);
  final values = <String, dynamic>{};

  final itemProperties = _properties(commandProperties['item_fields']);
  if (itemProperties.isNotEmpty) {
    values['item_fields'] = _publishedFieldValues(
      object.fieldSchema['item_fields'],
      itemProperties,
    );
  }

  final draftProperties = _properties(commandProperties['draft_listing']);
  if (draftProperties.isNotEmpty) {
    final draftValues = _publishedFieldValues(
      object.fieldSchema['listing_fields'],
      draftProperties,
    );
    final conditionSchema = _mapping(draftProperties['condition_enum']);
    if (conditionSchema.isNotEmpty) {
      final condition = _mapping(object.fieldSchema['condition']);
      final conditionValue = condition['value'];
      if (!_invalidEnumValue(conditionSchema, conditionValue)) {
        draftValues['condition_enum'] = conditionValue;
      }
    }
    values['draft_listing'] = draftValues;
  }

  return values;
}

Map<String, dynamic> _publishedFieldValues(
  dynamic rawFieldSchema,
  Map<String, dynamic> commandProperties,
) {
  final fields = _mapping(rawFieldSchema);
  final values = <String, dynamic>{};
  for (final entry in commandProperties.entries) {
    if (entry.key == 'condition_enum') continue;
    final field = _mapping(fields[entry.key]);
    if (!field.containsKey('value')) continue;
    final propertySchema = _mapping(entry.value);
    final value = field['value'];
    if (_invalidEnumValue(propertySchema, value)) continue;
    if (propertySchema['type'] == 'string-map' && value is Map) {
      values[entry.key] = {
        for (final mapEntry in value.entries)
          if (mapEntry.key is String &&
              (mapEntry.key as String).isNotEmpty &&
              mapEntry.value is String &&
              (mapEntry.value as String).isNotEmpty)
            mapEntry.key as String: mapEntry.value as String,
      };
      continue;
    }
    values[entry.key] = value;
  }
  return values;
}

bool _invalidEnumValue(Map<String, dynamic> schema, dynamic value) {
  final allowedValues = schema['enum'];
  return allowedValues is List &&
      (value == null || value == '' || !allowedValues.contains(value));
}

Map<String, dynamic> _properties(dynamic rawSchema) {
  final schema = _mapping(rawSchema);
  if (schema['type'] != 'object') return const {};
  return _mapping(schema['properties']);
}

Map<String, dynamic> _mapping(dynamic value) {
  return value is Map ? Map<String, dynamic>.from(value) : const {};
}
