import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:tgw_app/features/item/operator_submission_values.dart';
import 'package:tgw_app/models/models.dart';

void main() {
  test('Flutter renders the shared operator-object state matrix verbatim', () {
    final matrix = jsonDecode(
      File(
        '../../tests/fixtures/operator_object_state_matrix.json',
      ).readAsStringSync(),
    ) as List<dynamic>;

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
      expect({
        for (final command in object.commands)
          command.id: command.authorityScope,
      }, Map<String, String>.from(expected['authority_scopes'] as Map));
      final rawCommands = (Map<String, dynamic>.from(
        row['object'] as Map,
      )['commands'] as List)
          .map(
        (command) => Map<String, dynamic>.from(command as Map),
      );
      for (final command in object.commands) {
        final rawCommand = rawCommands.singleWhere(
          (candidate) => candidate['id'] == command.id,
        );
        expect(command.inputSchema, rawCommand['input_schema']);
        expect(command.valueSource, rawCommand['value_source']);
        expect(command.views, rawCommand['views']);
      }
      expect(
        object.fieldSchema.keys.toList()..sort(),
        List<String>.from(expected['field_schema_keys'] as List),
      );
      final saveDraft = object.commands.singleWhere(
        (command) => command.id == 'save-listing-draft',
      );
      expect(saveDraft.valueSource, 'editor');
      expect(saveDraft.views, ['listing']);
      expect(saveDraft.inputSchema['type'], 'object');
      final editorValues = initialOperatorEditorValues(object);
      expect(
        buildOperatorCommandValues(
          object: object,
          command: saveDraft,
          editorValues: editorValues,
          mediaOrder: const [],
        ),
        isA<Map<String, dynamic>>(),
      );

      final rawPresentation = Map<String, dynamic>.from(
        Map<String, dynamic>.from(
          row['object'] as Map,
        )['presentation'] as Map,
      );
      final rawMenus = (rawPresentation['action_menus'] as List? ?? const [])
          .map((entry) => Map<String, dynamic>.from(entry as Map))
          .toList(growable: false);
      expect(object.presentation.actionMenus.length, rawMenus.length);
      for (var index = 0; index < rawMenus.length; index++) {
        expect(
          object.presentation.actionMenus[index].commandIds,
          List<String>.from(rawMenus[index]['command_ids'] as List),
        );
        expect(
          object.presentation.actionMenus[index].defaultCommandId,
          rawMenus[index]['default_command_id'],
        );
      }
      expect(
        object.presentation.dataNavigation
            .map((entry) => {'label': entry.label, 'target': entry.target})
            .toList(growable: false),
        rawPresentation['data_navigation'],
      );

      final pricingCommand = object.commands.singleWhere(
        (command) => command.valueSource == 'pricing',
      );
      expect(
        buildOperatorCommandValues(
          object: object,
          command: pricingCommand,
          editorValues: editorValues,
          mediaOrder: const [],
          pricingSearchTerms: 'operator-entered exact query',
        ),
        {'search_terms': 'operator-entered exact query'},
      );
    }
  });

  test(
    'Flutter editor values follow the command schema and preserve aspect clears',
    () {
      final object = OperatorObjectView.fromJson({
        'entity_id': 'sparse-1',
        'object_generation': 'generation-1',
        'workflow': {
          'state': 'held',
          'reasons': ['taxonomy unavailable'],
        },
        'item': <String, dynamic>{},
        'listing': <String, dynamic>{},
        'field_schema': {
          'item_fields': {
            'title': {'type': 'string', 'value': 'Published title'},
            'notes': {'type': 'string', 'value': 'not in command schema'},
          },
          'listing_fields': {
            'title': {'type': 'string', 'value': 'Listing title'},
            'description': {'type': 'string', 'value': 'not in command schema'},
            'item_specifics': {
              'type': 'string-map',
              'value': {'Material': 'Silver', 'Brand': '', 'Null aspect': null},
            },
            'price': {'type': 'number', 'value': null},
          },
          'condition': {'value': '', 'options': <dynamic>[]},
          'aspects': [
            {
              'name': 'Brand',
              'value': '',
              'allowed_values': ['TGW'],
            },
          ],
        },
        'commands': [
          {
            'id': 'save-draft',
            'label': 'Save Draft',
            'enabled': true,
            'reason': null,
            'authority_scope': 'local-item-mutation',
            'value_source': 'editor',
            'views': ['inventory', 'listing'],
            'input_schema': {
              'type': 'object',
              'additionalProperties': false,
              'properties': {
                'item_fields': {
                  'type': 'object',
                  'additionalProperties': false,
                  'properties': {
                    'title': {'type': 'string'},
                  },
                },
                'draft_listing': {
                  'type': 'object',
                  'additionalProperties': false,
                  'properties': {
                    'title': {'type': 'string'},
                    'item_specifics': {'type': 'string-map'},
                    'price': {'type': 'number', 'nullable': true},
                  },
                },
              },
            },
          },
        ],
      });
      final command = object.commands.single;
      final commandWithPublishedCondition = OperatorCommandDescriptor.fromJson({
        'id': 'save-draft-with-condition',
        'label': 'Save Draft',
        'enabled': true,
        'reason': null,
        'authority_scope': 'local-item-mutation',
        'value_source': 'editor',
        'views': ['inventory', 'listing'],
        'input_schema': {
          'type': 'object',
          'additionalProperties': false,
          'properties': {
            'item_fields': {
              'type': 'object',
              'additionalProperties': false,
              'properties': {
                'title': {'type': 'string'},
              },
            },
            'draft_listing': {
              'type': 'object',
              'additionalProperties': false,
              'properties': {
                'title': {'type': 'string'},
                'condition_enum': {
                  'type': 'string',
                  'enum': ['USED_GOOD'],
                },
                'item_specifics': {'type': 'string-map'},
                'price': {'type': 'number', 'nullable': true},
              },
            },
          },
        },
      });

      final expected = {
        'item_fields': {'title': 'Published title'},
        'draft_listing': {
          'title': 'Listing title',
          'item_specifics': {
            'Material': 'Silver',
            'Brand': '',
            'Null aspect': '',
          },
          'price': null,
        },
      };
      final editorValues = initialOperatorEditorValues(object);
      expect(
        buildOperatorCommandValues(
          object: object,
          command: command,
          editorValues: editorValues,
          mediaOrder: const [],
        ),
        expected,
      );
      expect(
        buildOperatorCommandValues(
          object: object,
          command: commandWithPublishedCondition,
          editorValues: editorValues,
          mediaOrder: const [],
        ),
        expected,
      );

      object.fieldSchema['condition'] = {
        'value': 'NOT_PUBLISHED_IN_ENUM',
        'options': ['USED_GOOD'],
      };
      (editorValues['draft_listing']
          as Map<String, dynamic>)['condition_enum'] = 'NOT_PUBLISHED_IN_ENUM';
      expect(
        buildOperatorCommandValues(
          object: object,
          command: commandWithPublishedCondition,
          editorValues: editorValues,
          mediaOrder: const [],
        ),
        expected,
      );

      object.fieldSchema['condition'] = {
        'value': 'USED_GOOD',
        'options': ['USED_GOOD'],
      };
      (editorValues['draft_listing']
          as Map<String, dynamic>)['condition_enum'] = 'USED_GOOD';
      expect(
        buildOperatorCommandValues(
          object: object,
          command: command,
          editorValues: editorValues,
          mediaOrder: const [],
        ),
        expected,
      );
      expect(
        buildOperatorCommandValues(
          object: object,
          command: commandWithPublishedCondition,
          editorValues: editorValues,
          mediaOrder: const [],
        ),
        {
          'item_fields': {'title': 'Published title'},
          'draft_listing': {
            'title': 'Listing title',
            'condition_enum': 'USED_GOOD',
            'item_specifics': {
              'Material': 'Silver',
              'Brand': '',
              'Null aspect': '',
            },
            'price': null,
          },
        },
      );
    },
  );

  test(
    'List and Update submit every published field with selector nulls and explicit clears',
    () {
      final object = OperatorObjectView.fromJson({
        'entity_id': 'complete-1',
        'object_generation': 'generation-complete',
        'workflow': {'state': 'ready', 'reasons': <dynamic>[]},
        'item': <String, dynamic>{},
        'listing': <String, dynamic>{},
        'field_schema': {
          'listing_fields': {
            'title': {
              'type': 'string',
              'value': '',
              'required': true,
            },
            'secondary_category_id': {
              'type': 'string',
              'value': '',
              'nullable': true,
              'control': 'category-search',
            },
            'shipping_profile': {
              'type': 'string',
              'value': '',
              'required': true,
              'control': 'select',
            },
            'quantity': {
              'type': 'integer',
              'value': null,
              'nullable': true,
            },
            'item_specifics': {
              'type': 'string-map',
              'value': {'Material': 'Silver'},
            },
          },
          'condition': {
            'value': '',
            'required': true,
            'control': 'select',
            'options': [
              {'value': 'USED_GOOD', 'label': 'Used - Good'},
            ],
          },
          'aspects': [
            {'name': 'Brand', 'value': ''},
          ],
        },
        'commands': [
          for (final id in ['list-item', 'update-item'])
            {
              'id': id,
              'label': id,
              'enabled': true,
              'authority_scope': 'provider-effect',
              'value_source': 'editor',
              'views': ['listing'],
              'input_schema': {
                'type': 'object',
                'additionalProperties': false,
                'properties': {
                  'draft_listing': {
                    'type': 'object',
                    'additionalProperties': false,
                    'properties': {
                      'title': {'type': 'string'},
                      'secondary_category_id': {
                        'type': 'string',
                        'nullable': true,
                      },
                      'shipping_profile': {
                        'type': 'string',
                        'enum': ['', 'POLICY-1'],
                      },
                      'quantity': {'type': 'integer', 'nullable': true},
                      'condition_enum': {
                        'type': 'string',
                        'enum': ['USED_GOOD'],
                      },
                      'item_specifics': {'type': 'string-map'},
                    },
                  },
                },
              },
            },
        ],
      });
      final editorValues = initialOperatorEditorValues(object);
      (editorValues['draft_listing']
          as Map<String, dynamic>)['item_specifics'] = {
        'Material': 'Silver',
        'Brand': '',
        'Context-only aspect': '',
      };
      final expected = {
        'draft_listing': {
          'title': '',
          'secondary_category_id': null,
          'shipping_profile': '',
          'quantity': null,
          'condition_enum': '',
          'item_specifics': {
            'Material': 'Silver',
            'Brand': '',
            'Context-only aspect': '',
          },
        },
      };

      for (final command in object.commands) {
        expect(
          buildOperatorCommandValues(
            object: object,
            command: command,
            editorValues: editorValues,
            mediaOrder: const [],
            contextAspectNames: const ['Context-only aspect'],
          ),
          expected,
        );
      }
    },
  );

  test('Flutter preserves published pricing and navigation provenance', () {
    final object = OperatorObjectView.fromJson({
      'entity_id': 'priced-1',
      'object_generation': 'generation-priced',
      'workflow': {'state': 'ready', 'reasons': <dynamic>[]},
      'item': <String, dynamic>{},
      'listing': <String, dynamic>{},
      'field_schema': <String, dynamic>{},
      'commands': [
        {
          'id': 'reprice-item',
          'label': 'Run AI Pricer',
          'enabled': true,
          'authority_scope': 'local-workflow-request',
          'input_schema': {
            'type': 'object',
            'properties': {
              'search_terms': {'type': 'string'},
            },
          },
          'value_source': 'pricing',
          'views': ['listing'],
        },
      ],
      'presentation': {
        'listing_editor': {'id': 'listing-editor'},
        'action_menus': [
          {
            'id': 'primary',
            'label': 'Listing actions',
            'command_ids': ['reprice-item'],
            'default_command_id': 'reprice-item',
            'views': ['listing'],
          },
        ],
        'data_navigation': [
          {'label': 'Pricing', 'target': 'pricing-data'},
        ],
        'pricing_context': {
          'id': 'pricing-data',
          'command_id': 'reprice-item',
          'search_terms': 'winning query',
          'search_terms_source': 'pricing-observation',
          'requested_search_terms': 'operator request',
          'last_successful_search_terms': 'winning query',
          'research_links': [
            {
              'id': 'sold',
              'label': 'Sold',
              'href': 'https://example.invalid/sold',
              'external': true,
            },
          ],
        },
      },
    });

    final pricing = object.presentation.pricingContext;
    expect(pricing.commandId, 'reprice-item');
    expect(pricing.searchTerms, 'winning query');
    expect(pricing.searchTermsSource, 'pricing-observation');
    expect(pricing.requestedSearchTerms, 'operator request');
    expect(pricing.lastSuccessfulSearchTerms, 'winning query');
    expect(pricing.researchLinks.single.href, 'https://example.invalid/sold');
    expect(object.presentation.listingEditorId, 'listing-editor');
    expect(object.presentation.actionMenus.single.defaultCommandId,
        'reprice-item');
    expect(object.presentation.dataNavigation.single.target, 'pricing-data');

    final command = object.commands.single;
    final editorValues = initialOperatorEditorValues(object);
    expect(
      buildOperatorCommandValues(
        object: object,
        command: command,
        editorValues: editorValues,
        mediaOrder: const [],
      ),
      {'search_terms': 'winning query'},
    );
    expect(
      buildOperatorCommandValues(
        object: object,
        command: command,
        editorValues: editorValues,
        mediaOrder: const [],
        pricingSearchTerms: 'edited current query',
      ),
      {'search_terms': 'edited current query'},
    );
  });
}
