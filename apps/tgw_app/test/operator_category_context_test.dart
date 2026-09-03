import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tgw_app/api/api_client.dart';
import 'package:tgw_app/features/item/item_screen.dart';
import 'package:tgw_app/models/models.dart';
import 'package:tgw_app/providers/providers.dart';

void main() {
  testWidgets(
    'category selection refreshes published controls before List submission',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(1200, 1500));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final object = _operatorObject();
      final api = _CategoryContextApi(object);

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            apiClientProvider.overrideWithValue(api),
            connectionStatusProvider.overrideWith(
              (ref) => _AlwaysOnlineNotifier(api, ref),
            ),
          ],
          child: const MaterialApp(
            home: Scaffold(body: ItemScreen(sku: 'item-1')),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final categorySearch = find.byWidgetPredicate(
        (widget) =>
            widget is TextField &&
            widget.decoration?.labelText == 'Search by category name or ID',
      );
      expect(categorySearch, findsOneWidget);
      await tester.enterText(categorySearch, '456');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Collectibles › New category'));
      await tester.pumpAndSettle();

      expect(
        api.dataEndpoints,
        containsAllInOrder([
          '/api/category-node/456',
          '/api/category-context/456?condition=USED_GOOD&sku=item-1',
        ]),
      );
      expect(find.text('Context-only aspect'), findsOneWidget);
      expect(find.text('Refurbished'), findsWidgets);

      await tester.ensureVisible(find.text('Execute'));
      await tester.tap(find.text('Execute'));
      await tester.pumpAndSettle();

      expect(api.submittedCommand, 'list-item');
      final draft = Map<String, dynamic>.from(
        api.submittedValues?['draft_listing'] as Map,
      );
      expect(draft.keys, {
        'title',
        'category_id',
        'condition_enum',
        'item_specifics',
      });
      expect(draft['category_id'], '456');
      expect(draft['condition_enum'], 'REFURBISHED');
      expect(
        draft['item_specifics'],
        {'Brand': 'TGW', 'Context-only aspect': ''},
      );
    },
  );
}

OperatorObjectView _operatorObject() {
  return OperatorObjectView.fromJson({
    'entity_id': 'item-1',
    'object_generation': 'generation-1',
    'workflow': {'state': 'ready', 'reasons': <dynamic>[]},
    'item': <String, dynamic>{},
    'listing': <String, dynamic>{},
    'field_schema': {
      'listing_fields': {
        'title': {'type': 'string', 'value': 'Published title'},
        'category_id': {
          'type': 'string',
          'value': '123',
          'label': 'eBay category',
          'control': 'category-search',
          'lookup': {
            'node_endpoint': '/api/category-node/{value}',
            'context_endpoint':
                '/api/category-context/{value}?condition={current_condition}&sku={sku}',
            'minimum_query_length': 2,
          },
          'selection': {
            'value': '123',
            'label': 'Old category',
            'path': ['Collectibles', 'Old category'],
          },
        },
        'item_specifics': {
          'type': 'string-map',
          'value': {'Brand': 'TGW'},
        },
      },
      'condition': {
        'value': 'USED_GOOD',
        'label': 'eBay condition',
        'control': 'select',
        'required': true,
        'options': [
          {'value': 'USED_GOOD', 'label': 'Used - Good'},
        ],
      },
      'aspects': [
        {'name': 'Brand', 'value': 'TGW', 'custom': true},
      ],
    },
    'commands': [
      {
        'id': 'list-item',
        'label': 'List item',
        'enabled': true,
        'authority_scope': 'provider-effect',
        'value_source': 'editor',
        'views': ['listing'],
        'tone': 'primary',
        'input_schema': {
          'type': 'object',
          'additionalProperties': false,
          'properties': {
            'draft_listing': {
              'type': 'object',
              'additionalProperties': false,
              'properties': {
                'title': {'type': 'string'},
                'category_id': {'type': 'string'},
                'condition_enum': {
                  'type': 'string',
                  'enum': ['USED_GOOD', 'REFURBISHED'],
                },
                'item_specifics': {'type': 'string-map'},
              },
            },
          },
        },
      },
    ],
    'presentation': {
      'title': 'Published title',
      'views': [
        {
          'id': 'listing',
          'label': 'Listing',
          'default': true,
          'layout': 'document',
          'regions': [
            {
              'id': 'primary',
              'components': ['listing-editor', 'commands'],
            },
          ],
        },
      ],
      'listing_editor': {'id': 'listing-editor'},
      'action_menus': [
        {
          'id': 'listing-actions',
          'label': 'Listing actions',
          'command_ids': ['list-item'],
          'default_command_id': 'list-item',
          'views': ['listing'],
        },
      ],
    },
  });
}

class _CategoryContextApi extends ApiClient {
  final OperatorObjectView object;
  final List<String> dataEndpoints = [];
  String? submittedCommand;
  Map<String, dynamic>? submittedValues;

  _CategoryContextApi(this.object);

  @override
  Future<ApiResponse<OperatorObjectView>> getOperatorObject(String sku) async {
    return ApiResponse(ok: true, data: object);
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> getPublishedOperatorData(
    String endpoint,
  ) async {
    dataEndpoints.add(endpoint);
    if (endpoint == '/api/category-node/456') {
      return ApiResponse(
        ok: true,
        data: {
          'ok': true,
          'category_id': '456',
          'category_name': 'New category',
          'path': ['Collectibles', 'New category'],
          'leaf': true,
        },
      );
    }
    if (endpoint ==
        '/api/category-context/456?condition=USED_GOOD&sku=item-1') {
      return ApiResponse(
        ok: true,
        data: {
          'ok': true,
          'category_name': 'New category',
          'conditions': [
            {'enum': 'REFURBISHED', 'label': 'Refurbished'},
          ],
          'condition_remap': {
            'enum': 'REFURBISHED',
            'label': 'Refurbished',
          },
          'aspects': [
            {
              'name': 'Context-only aspect',
              'required': true,
              'allowed_values': ['Blue', 'Red'],
              'value': '',
            },
          ],
          'aspects_error': null,
        },
      );
    }
    return ApiResponse(ok: false, error: 'Unexpected endpoint: $endpoint');
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> executeOperatorCommand(
    String sku,
    OperatorCommandDescriptor command,
    String objectGeneration,
    Map<String, dynamic> values,
  ) async {
    submittedCommand = command.id;
    submittedValues = values;
    return ApiResponse(ok: true, data: {'ok': true});
  }
}

class _AlwaysOnlineNotifier extends ConnectionStatusNotifier {
  // The super constructor's positional parameter names are library-private.
  // ignore: use_super_parameters
  _AlwaysOnlineNotifier(ApiClient apiClient, Ref ref) : super(apiClient, ref);

  @override
  Future<void> checkConnection() async {
    state = ConnectionStatus.online;
  }
}
