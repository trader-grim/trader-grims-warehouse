import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/providers.dart';

/// Mobile projection of the single PlanAuthority service.
///
/// It intentionally contains no executor control: only the separately
/// authenticated registered executor may redeem an approved typed effect.
class PlanAuthorityScreen extends ConsumerStatefulWidget {
  const PlanAuthorityScreen({super.key});

  @override
  ConsumerState<PlanAuthorityScreen> createState() =>
      _PlanAuthorityScreenState();
}

class _PlanAuthorityScreenState extends ConsumerState<PlanAuthorityScreen> {
  List<Map<String, dynamic>> _requests = const [];
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final response =
        await ref.read(apiClientProvider).getPlanAuthorityRequests();
    if (!mounted) return;
    setState(() {
      _loading = false;
      _requests = response.data ?? const [];
      _error = response.ok
          ? null
          : (response.error ?? 'PlanAuthority is unavailable');
    });
  }

  Future<void> _decide(Map<String, dynamic> request, String kind) async {
    final input = await showDialog<_DecisionInput>(
      context: context,
      builder: (_) => _DecisionDialog(kind: kind),
    );
    if (input == null) return;

    final requestId = request['request_id'];
    if (requestId is! String || requestId.isEmpty) {
      return;
    }
    final response =
        await ref.read(apiClientProvider).decidePlanAuthorityRequest(
              requestId,
              kind: kind,
              reason: input.reason,
              reconciliationEvidence: input.evidence,
            );
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(response.ok
          ? 'Decision recorded'
          : (response.error ?? 'Decision was rejected')),
    ));
    if (response.ok) await _reload();
  }

  Future<void> _createDevelopmentRequest() async {
    final input = await showDialog<_DevelopmentInput>(
      context: context,
      builder: (_) => const _DevelopmentDialog(),
    );
    if (input == null) return;
    final response =
        await ref.read(apiClientProvider).createDevelopmentRequest({
      'schema': 'tgw-development-console-request/v1',
      'original_request': input.request,
      'scope': input.scope,
      'constraints': input.constraints,
      'effect_limits': input.effectLimits,
      if (input.rootKind != null)
        'root': {'kind': input.rootKind, 'id': input.rootId},
    });
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(response.ok
          ? 'Development request retained'
          : (response.error ?? 'Development request was held')),
    ));
    if (response.ok) await _reload();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(_error!, textAlign: TextAlign.center),
        ),
      );
    }
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
          child: SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: _createDevelopmentRequest,
              icon: const Icon(Icons.add_task),
              label: const Text('Start development work'),
            ),
          ),
        ),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _reload,
            child: _requests.isEmpty
                ? ListView(children: const [
                    SizedBox(height: 160),
                    Center(child: Text('No PlanAuthority requests.')),
                  ])
                : ListView.builder(
                    padding: const EdgeInsets.all(12),
                    itemCount: _requests.length,
                    itemBuilder: (context, index) => _AuthorityCard(
                      request: _requests[index],
                      decide: _decide,
                    ),
                  ),
          ),
        ),
      ],
    );
  }
}

class _AuthorityCard extends StatelessWidget {
  final Map<String, dynamic> request;
  final Future<void> Function(Map<String, dynamic>, String) decide;

  const _AuthorityCard({required this.request, required this.decide});

  @override
  Widget build(BuildContext context) {
    final status = (request['status'] ??
            request['outcome'] ??
            request['decision_kind'] ??
            'pending')
        .toString();
    final effect = request['effect'];
    final effectKind = effect is Map ? effect['kind'] : request['effect_kind'];
    final effectMap = effect is Map
        ? Map<String, dynamic>.from(effect)
        : const <String, dynamic>{};
    final decision = request['decision'];
    final decisionMap = decision is Map
        ? Map<String, dynamic>.from(decision)
        : const <String, dynamic>{};
    final execution = request['execution'];
    final executionMap = execution is Map
        ? Map<String, dynamic>.from(execution)
        : const <String, dynamic>{};
    final legalActions = request['legal_actions'];
    final canDecide = legalActions is List
        ? legalActions.map((action) => action.toString()).toSet()
        : <String>{if (status == 'pending') 'approve', 'hold', 'reconcile'};
    final requestId = request['request_id']?.toString() ?? 'unknown request';
    final development = request['development'];
    final developmentMap = development is Map
        ? Map<String, dynamic>.from(development)
        : const <String, dynamic>{};
    final resolution = developmentMap['resolution'];
    final resolutionMap = resolution is Map
        ? Map<String, dynamic>.from(resolution)
        : const <String, dynamic>{};
    final launchCards = developmentMap['launch_cards'] is List
        ? developmentMap['launch_cards'] as List
        : const [];

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
                request['summary']?.toString() ??
                    effectKind?.toString() ??
                    'Plan effect',
                style: const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text('Status: $status'),
            Text('Request: $requestId',
                style: Theme.of(context).textTheme.bodySmall),
            if (effectKind != null)
              Text('Effect: $effectKind',
                  style: Theme.of(context).textTheme.bodySmall),
            if (developmentMap.isNotEmpty) ...[
              const SizedBox(height: 8),
              const Text('Development resolution',
                  style: TextStyle(fontWeight: FontWeight.bold)),
              _AuthorityDetail(
                  label: 'Original request',
                  value:
                      (developmentMap['request'] as Map?)?['original_request']),
              _AuthorityDetail(
                  label: 'Scope',
                  value: (developmentMap['request'] as Map?)?['scope']),
              _AuthorityDetail(
                  label: 'Resolution', value: resolutionMap['status']),
              _AuthorityDetail(
                  label: 'Explanation', value: resolutionMap['explanation']),
              if (resolutionMap['clarification'] != null)
                _AuthorityDetail(
                    label: 'Clarification',
                    value: resolutionMap['clarification']),
              _AuthorityDetail(
                  label: 'Lifecycle',
                  value: developmentMap['lifecycle_hash'],
                  mono: true),
              const SizedBox(height: 4),
              ...launchCards.map((raw) {
                final launch = raw is Map
                    ? Map<String, dynamic>.from(raw)
                    : const <String, dynamic>{};
                final selection = launch['provider_selection'] is Map
                    ? Map<String, dynamic>.from(
                        launch['provider_selection'] as Map)
                    : const <String, dynamic>{};
                return ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text(
                      '${launch['unit'] ?? 'unit'} — ${launch['role'] ?? 'role'}'),
                  subtitle: Text(
                    '${selection['mode'] ?? 'qualified provider at launch'}\n${(launch['allocation'] as Map?)?['worktree'] ?? ''}',
                  ),
                );
              }),
            ],
            const SizedBox(height: 8),
            const Text('Exact effect scope',
                style: TextStyle(fontWeight: FontWeight.bold)),
            _AuthorityDetail(label: 'Kind', value: effectMap['kind']),
            _AuthorityDetail(
                label: 'Generation', value: effectMap['generation']),
            _AuthorityDetail(
                label: 'Effect hash', value: effectMap['hash'], mono: true),
            _AuthorityDetail(
              label: 'Parameters',
              value: const JsonEncoder.withIndent('  ')
                  .convert(effectMap['parameters'] ?? const {}),
              mono: true,
            ),
            const SizedBox(height: 8),
            const Text('Bound Plan solution',
                style: TextStyle(fontWeight: FontWeight.bold)),
            _AuthorityDetail(
                label: 'Requested by', value: request['requested_by']),
            _AuthorityDetail(
                label: 'Plan commit',
                value: request['plan_commit'],
                mono: true),
            _AuthorityDetail(
                label: 'Solution hash',
                value: request['solution_hash'],
                mono: true),
            _AuthorityDetail(
                label: 'Closure hash',
                value: request['closure_hash'],
                mono: true),
            _AuthorityDetail(label: 'Graph', value: request['graph_id']),
            _AuthorityDetail(
                label: 'Object generation',
                value: request['object_generation']),
            _AuthorityDetail(
                label: 'Evidence',
                value: _identityList(request['evidence']),
                mono: true),
            const SizedBox(height: 8),
            const Text('Decision',
                style: TextStyle(fontWeight: FontWeight.bold)),
            _AuthorityDetail(label: 'Kind', value: decisionMap['kind']),
            _AuthorityDetail(label: 'Principal', value: decisionMap['by']),
            _AuthorityDetail(label: 'Reason', value: decisionMap['reason']),
            _AuthorityDetail(label: 'At', value: decisionMap['at']),
            _AuthorityDetail(
              label: 'Reconciliation evidence',
              value: _identityList(decisionMap['reconciliation_evidence']),
              mono: true,
            ),
            const SizedBox(height: 8),
            const Text('Execution / receipt provenance',
                style: TextStyle(fontWeight: FontWeight.bold)),
            _AuthorityDetail(
                label: 'Receipt',
                value: executionMap['receipt_id'] ?? request['receipt_id'],
                mono: true),
            _AuthorityDetail(
                label: 'Executor principal',
                value: executionMap['executor_principal']),
            _AuthorityDetail(
                label: 'Handler', value: executionMap['handler_id']),
            _AuthorityDetail(
                label: 'Started', value: executionMap['started_at']),
            _AuthorityDetail(
                label: 'Completed', value: executionMap['completed_at']),
            _AuthorityDetail(label: 'Outcome', value: executionMap['outcome']),
            _AuthorityDetail(
                label: 'Evidence',
                value: _identityList(executionMap['evidence']),
                mono: true),
            _AuthorityDetail(
                label: 'Rollback receipt',
                value: executionMap['rollback_receipt'],
                mono: true),
            _AuthorityDetail(label: 'Detail', value: executionMap['detail']),
            const SizedBox(height: 8),
            const Text('Authenticated operator decision',
                style: TextStyle(fontWeight: FontWeight.bold)),
            Wrap(
              spacing: 8,
              children: [
                if (canDecide.contains('approve'))
                  FilledButton(
                      onPressed: () => decide(request, 'approve'),
                      child: const Text('Approve')),
                if (canDecide.contains('hold'))
                  OutlinedButton(
                      onPressed: () => decide(request, 'hold'),
                      child: const Text('Hold')),
                if (canDecide.contains('reconcile'))
                  OutlinedButton(
                      onPressed: () => decide(request, 'reconcile'),
                      child: const Text('Reconcile')),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

String _identityList(dynamic value) {
  if (value is! List || value.isEmpty) return '—';
  return value.map((item) => item.toString()).join('\n');
}

class _AuthorityDetail extends StatelessWidget {
  final String label;
  final dynamic value;
  final bool mono;

  const _AuthorityDetail(
      {required this.label, required this.value, this.mono = false});

  @override
  Widget build(BuildContext context) {
    final text =
        value == null || value.toString().isEmpty ? '—' : value.toString();
    final style = mono
        ? Theme.of(context)
            .textTheme
            .bodySmall
            ?.copyWith(fontFamily: 'monospace')
        : null;
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
              width: 150,
              child: Text('$label:',
                  style: Theme.of(context).textTheme.bodySmall)),
          Expanded(child: SelectableText(text, style: style)),
        ],
      ),
    );
  }
}

class _DecisionInput {
  final String reason;
  final List<String> evidence;

  const _DecisionInput({required this.reason, required this.evidence});
}

class _DevelopmentInput {
  final String request;
  final String scope;
  final List<String> constraints;
  final List<String> effectLimits;
  final String? rootKind;
  final String? rootId;

  const _DevelopmentInput({
    required this.request,
    required this.scope,
    required this.constraints,
    required this.effectLimits,
    this.rootKind,
    this.rootId,
  });
}

class _DevelopmentDialog extends StatefulWidget {
  const _DevelopmentDialog();

  @override
  State<_DevelopmentDialog> createState() => _DevelopmentDialogState();
}

class _DevelopmentDialogState extends State<_DevelopmentDialog> {
  final _request = TextEditingController();
  final _scope = TextEditingController();
  final _constraints = TextEditingController();
  final _effects = TextEditingController();
  final _rootId = TextEditingController();
  String? _rootKind;

  List<String> _lines(TextEditingController controller) => controller.text
      .split(RegExp(r'\r?\n'))
      .map((item) => item.trim())
      .where((item) => item.isNotEmpty)
      .toList(growable: false);

  @override
  void dispose() {
    _request.dispose();
    _scope.dispose();
    _constraints.dispose();
    _effects.dispose();
    _rootId.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Start development work'),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: _request,
                autofocus: true,
                minLines: 3,
                maxLines: 8,
                decoration: const InputDecoration(
                    labelText: 'What should be built?',
                    border: OutlineInputBorder()),
              ),
              const SizedBox(height: 12),
              TextField(
                  controller: _scope,
                  decoration: const InputDecoration(
                      labelText: 'Scope', border: OutlineInputBorder())),
              const SizedBox(height: 12),
              TextField(
                controller: _constraints,
                minLines: 2,
                maxLines: 5,
                decoration: const InputDecoration(
                    labelText: 'Constraints (one per line)',
                    border: OutlineInputBorder()),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _effects,
                minLines: 2,
                maxLines: 5,
                decoration: const InputDecoration(
                    labelText: 'Allowed effects (one per line)',
                    border: OutlineInputBorder()),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String?>(
                initialValue: _rootKind,
                decoration: const InputDecoration(
                    labelText: 'Narrower root (optional)',
                    border: OutlineInputBorder()),
                items: const [
                  DropdownMenuItem(value: null, child: Text('Approved Plan')),
                  DropdownMenuItem(value: 'PP', child: Text('PP')),
                  DropdownMenuItem(value: 'Todo', child: Text('Todo')),
                ],
                onChanged: (value) => setState(() => _rootKind = value),
              ),
              if (_rootKind != null) ...[
                const SizedBox(height: 12),
                TextField(
                    controller: _rootId,
                    decoration: const InputDecoration(
                        labelText: 'Exact root ID',
                        border: OutlineInputBorder())),
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel')),
        FilledButton(
          onPressed: () {
            final request = _request.text.trim();
            final scope = _scope.text.trim();
            final rootId = _rootId.text.trim();
            if (request.isEmpty ||
                scope.isEmpty ||
                (_rootKind != null && rootId.isEmpty)) {
              return;
            }
            Navigator.pop(
                context,
                _DevelopmentInput(
                  request: request,
                  scope: scope,
                  constraints: _lines(_constraints),
                  effectLimits: _lines(_effects),
                  rootKind: _rootKind,
                  rootId: _rootKind == null ? null : rootId,
                ));
          },
          child: const Text('Resolve'),
        ),
      ],
    );
  }
}

class _DecisionDialog extends StatefulWidget {
  final String kind;

  const _DecisionDialog({required this.kind});

  @override
  State<_DecisionDialog> createState() => _DecisionDialogState();
}

class _DecisionDialogState extends State<_DecisionDialog> {
  final _reason = TextEditingController();
  final _evidence = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    _evidence.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final requiresEvidence = widget.kind == 'reconcile';
    return AlertDialog(
      title: Text(
          '${widget.kind[0].toUpperCase()}${widget.kind.substring(1)} authority request'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _reason,
            autofocus: true,
            decoration: const InputDecoration(
                labelText: 'Reason', border: OutlineInputBorder()),
          ),
          if (requiresEvidence) ...[
            const SizedBox(height: 12),
            TextField(
              controller: _evidence,
              decoration: const InputDecoration(
                labelText: 'Reconciliation evidence (one identity per line)',
                border: OutlineInputBorder(),
              ),
              minLines: 2,
              maxLines: 4,
            ),
          ],
        ],
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel')),
        FilledButton(
          onPressed: () {
            final reason = _reason.text.trim();
            final evidence = _evidence.text
                .split(RegExp(r'\r?\n'))
                .map((item) => item.trim())
                .where((item) => item.isNotEmpty)
                .toList(growable: false);
            if (reason.isEmpty || (requiresEvidence && evidence.isEmpty)) {
              return;
            }
            Navigator.pop(
                context, _DecisionInput(reason: reason, evidence: evidence));
          },
          child: Text(widget.kind),
        ),
      ],
    );
  }
}
