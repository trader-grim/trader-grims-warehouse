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
  ConsumerState<PlanAuthorityScreen> createState() => _PlanAuthorityScreenState();
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
    final response = await ref.read(apiClientProvider).getPlanAuthorityRequests();
    if (!mounted) return;
    setState(() {
      _loading = false;
      _requests = response.data ?? const [];
      _error = response.ok ? null : (response.error ?? 'PlanAuthority is unavailable');
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
    final response = await ref.read(apiClientProvider).decidePlanAuthorityRequest(
      requestId,
      kind: kind,
      reason: input.reason,
      reconciliationEvidence: input.evidence,
    );
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(response.ok ? 'Decision recorded' : (response.error ?? 'Decision was rejected')),
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
    return RefreshIndicator(
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
    );
  }
}

class _AuthorityCard extends StatelessWidget {
  final Map<String, dynamic> request;
  final Future<void> Function(Map<String, dynamic>, String) decide;

  const _AuthorityCard({required this.request, required this.decide});

  @override
  Widget build(BuildContext context) {
    final status = (request['status'] ?? request['outcome'] ?? request['decision_kind'] ?? 'pending').toString();
    final effect = request['effect'];
    final effectKind = effect is Map ? effect['kind'] : request['effect_kind'];
    final effectMap = effect is Map ? Map<String, dynamic>.from(effect) : const <String, dynamic>{};
    final decision = request['decision'];
    final decisionMap = decision is Map ? Map<String, dynamic>.from(decision) : const <String, dynamic>{};
    final execution = request['execution'];
    final executionMap = execution is Map ? Map<String, dynamic>.from(execution) : const <String, dynamic>{};
    final legalActions = request['legal_actions'];
    final canDecide = legalActions is List
        ? legalActions.map((action) => action.toString()).toSet()
        : <String>{if (status == 'pending') 'approve', 'hold', 'reconcile'};
    final requestId = request['request_id']?.toString() ?? 'unknown request';

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(request['summary']?.toString() ?? effectKind?.toString() ?? 'Plan effect',
                style: const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text('Status: $status'),
            Text('Request: $requestId', style: Theme.of(context).textTheme.bodySmall),
            if (effectKind != null) Text('Effect: $effectKind', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 8),
            const Text('Exact effect scope', style: TextStyle(fontWeight: FontWeight.bold)),
            _AuthorityDetail(label: 'Kind', value: effectMap['kind']),
            _AuthorityDetail(label: 'Generation', value: effectMap['generation']),
            _AuthorityDetail(label: 'Effect hash', value: effectMap['hash'], mono: true),
            _AuthorityDetail(
              label: 'Parameters',
              value: const JsonEncoder.withIndent('  ').convert(effectMap['parameters'] ?? const {}),
              mono: true,
            ),
            const SizedBox(height: 8),
            const Text('Bound Plan solution', style: TextStyle(fontWeight: FontWeight.bold)),
            _AuthorityDetail(label: 'Requested by', value: request['requested_by']),
            _AuthorityDetail(label: 'Plan commit', value: request['plan_commit'], mono: true),
            _AuthorityDetail(label: 'Solution hash', value: request['solution_hash'], mono: true),
            _AuthorityDetail(label: 'Closure hash', value: request['closure_hash'], mono: true),
            _AuthorityDetail(label: 'Graph', value: request['graph_id']),
            _AuthorityDetail(label: 'Object generation', value: request['object_generation']),
            _AuthorityDetail(label: 'Evidence', value: _identityList(request['evidence']), mono: true),
            const SizedBox(height: 8),
            const Text('Decision', style: TextStyle(fontWeight: FontWeight.bold)),
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
            const Text('Execution / receipt provenance', style: TextStyle(fontWeight: FontWeight.bold)),
            _AuthorityDetail(label: 'Receipt', value: executionMap['receipt_id'] ?? request['receipt_id'], mono: true),
            _AuthorityDetail(label: 'Executor principal', value: executionMap['executor_principal']),
            _AuthorityDetail(label: 'Handler', value: executionMap['handler_id']),
            _AuthorityDetail(label: 'Started', value: executionMap['started_at']),
            _AuthorityDetail(label: 'Completed', value: executionMap['completed_at']),
            _AuthorityDetail(label: 'Outcome', value: executionMap['outcome']),
            _AuthorityDetail(label: 'Evidence', value: _identityList(executionMap['evidence']), mono: true),
            _AuthorityDetail(label: 'Rollback receipt', value: executionMap['rollback_receipt'], mono: true),
            _AuthorityDetail(label: 'Detail', value: executionMap['detail']),
            const SizedBox(height: 8),
            const Text('Authenticated operator decision', style: TextStyle(fontWeight: FontWeight.bold)),
            Wrap(
              spacing: 8,
              children: [
                if (canDecide.contains('approve'))
                  FilledButton(onPressed: () => decide(request, 'approve'), child: const Text('Approve')),
                if (canDecide.contains('hold'))
                  OutlinedButton(onPressed: () => decide(request, 'hold'), child: const Text('Hold')),
                if (canDecide.contains('reconcile'))
                  OutlinedButton(onPressed: () => decide(request, 'reconcile'), child: const Text('Reconcile')),
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

  const _AuthorityDetail({required this.label, required this.value, this.mono = false});

  @override
  Widget build(BuildContext context) {
    final text = value == null || value.toString().isEmpty ? '—' : value.toString();
    final style = mono ? Theme.of(context).textTheme.bodySmall?.copyWith(fontFamily: 'monospace') : null;
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 150, child: Text('$label:', style: Theme.of(context).textTheme.bodySmall)),
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
      title: Text('${widget.kind[0].toUpperCase()}${widget.kind.substring(1)} authority request'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _reason,
            autofocus: true,
            decoration: const InputDecoration(labelText: 'Reason', border: OutlineInputBorder()),
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
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          onPressed: () {
            final reason = _reason.text.trim();
            final evidence = _evidence.text
                .split(RegExp(r'\r?\n'))
                .map((item) => item.trim())
                .where((item) => item.isNotEmpty)
                .toList(growable: false);
            if (reason.isEmpty || (requiresEvidence && evidence.isEmpty)) return;
            Navigator.pop(context, _DecisionInput(reason: reason, evidence: evidence));
          },
          child: Text(widget.kind),
        ),
      ],
    );
  }
}
