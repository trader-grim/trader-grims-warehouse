import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:tgw_app/models/models.dart';

void main() {
  test('Flutter renders the shared operator-object state matrix verbatim', () {
    final matrix =
        jsonDecode(
              File(
                '../../tests/fixtures/operator_object_state_matrix.json',
              ).readAsStringSync(),
            )
            as List<dynamic>;

    for (final raw in matrix) {
      final row = Map<String, dynamic>.from(raw as Map);
      final object = OperatorObjectView.fromJson(
        Map<String, dynamic>.from(row['object'] as Map),
      );
      final expected = Map<String, dynamic>.from(row['expected'] as Map);
      expect(object.state, expected['state']);
      expect(object.reasons, List<String>.from(expected['reasons'] as List));
      expect(
        object.commands
            .where((command) => command.enabled)
            .map((command) => command.id)
            .toList(),
        List<String>.from(expected['enabled_commands'] as List),
      );
      expect(
        {
          for (final command in object.commands)
            command.id: command.authorityScope,
        },
        Map<String, String>.from(expected['authority_scopes'] as Map),
      );
      expect(
        object.fieldSchema.keys.toList()..sort(),
        List<String>.from(expected['field_schema_keys'] as List),
      );
    }
  });
}
