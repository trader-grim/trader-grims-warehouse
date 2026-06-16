import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/providers.dart';
import 'pipeline_job_sheet.dart';

class HomeScreen extends ConsumerStatefulWidget {
  final Function(String) onSkuLookup;
  const HomeScreen({super.key, required this.onSkuLookup});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  final _skuController = TextEditingController();

  @override
  void dispose() {
    _skuController.dispose();
    super.dispose();
  }

  void _openJobSheet({String? queueName, String? filterState, required String title}) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => PipelineJobSheet(
        title: title,
        queueName: queueName,
        filterState: filterState,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final queueStatusAsync = ref.watch(queueStatusProvider);
    final pipelineJobsAsync = ref.watch(pipelineJobsProvider);

    final stuckByQueue = <String, int>{};
    pipelineJobsAsync.whenData((jobs) {
      for (final j in jobs) {
        if (j.isStuck) stuckByQueue[j.queueName] = (stuckByQueue[j.queueName] ?? 0) + 1;
      }
    });

    final totalStuck = stuckByQueue.values.fold(0, (s, c) => s + c);

    return RefreshIndicator(
      onRefresh: () async {
        ref.invalidate(queueStatusProvider);
        ref.invalidate(pipelineJobsProvider);
      },
      child: ListView(
        padding: const EdgeInsets.all(16.0),
        children: [
          const Text('Quick Lookup', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _skuController,
                  decoration: const InputDecoration(
                    hintText: 'Enter SKU (e.g. tgw...)',
                    border: OutlineInputBorder(),
                    contentPadding: EdgeInsets.symmetric(horizontal: 12),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              ElevatedButton(
                onPressed: () {
                  if (_skuController.text.isNotEmpty) {
                    widget.onSkuLookup(_skuController.text.trim());
                    _skuController.clear();
                  }
                },
                child: const Text('Go'),
              ),
            ],
          ),
          const SizedBox(height: 32),

          // ── Stuck jobs banner ─────────────────────────────────────────────
          if (totalStuck > 0) ...[
            GestureDetector(
              onTap: () => _openJobSheet(
                filterState: null,
                title: 'Stuck Active Jobs',
              ),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Colors.yellow[900]!.withValues(alpha: 0.25),
                  border: Border.all(color: Colors.yellow[700]!),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.warning_amber, color: Colors.yellow, size: 20),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '$totalStuck stuck active job${totalStuck == 1 ? '' : 's'} — tap to inspect',
                        style: const TextStyle(color: Colors.yellow, fontSize: 13),
                      ),
                    ),
                    const Icon(Icons.chevron_right, color: Colors.yellow, size: 18),
                  ],
                ),
              ),
            ),
          ],

          Row(
            children: [
              const Text('Queue Status', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
              const Spacer(),
              pipelineJobsAsync.when(
                data: (_) => const SizedBox.shrink(),
                loading: () => const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 1.5)),
                error: (_, __) => const Icon(Icons.cloud_off, size: 16, color: Colors.grey),
              ),
            ],
          ),
          const SizedBox(height: 16),

          queueStatusAsync.when(
            data: (status) {
              if (status == null || status.queues.isEmpty) {
                return const Card(
                  child: Padding(
                    padding: EdgeInsets.all(16.0),
                    child: Text('No queue data available or server offline.'),
                  ),
                );
              }

              return GridView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                  maxCrossAxisExtent: 210,
                  mainAxisSpacing: 10,
                  crossAxisSpacing: 10,
                  childAspectRatio: 1.3,
                ),
                itemCount: status.queues.length,
                itemBuilder: (context, index) {
                  final queueName = status.queues.keys.elementAt(index);
                  final states = status.queues[queueName]!;
                  final stuck = stuckByQueue[queueName] ?? 0;
                  return _QueueCard(
                    queueName: queueName,
                    states: states,
                    stuckCount: stuck,
                    onStateTap: (state, title) => _openJobSheet(
                      queueName: queueName,
                      filterState: state,
                      title: title,
                    ),
                    onStuckTap: () => _openJobSheet(
                      queueName: queueName,
                      filterState: null,
                      title: '$queueName — stuck',
                    ),
                  );
                },
              );
            },
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (err, stack) => Card(
              color: Colors.red[50],
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Text('Error: $err'),
              ),
            ),
          ),

          const SizedBox(height: 32),
          const Text('Quick Actions', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          const Card(
            child: ListTile(
              leading: Icon(Icons.add_photo_alternate),
              title: Text('Start Photo Intake'),
              subtitle: Text('// TODO Phase D'),
              enabled: false,
            ),
          ),
          const Card(
            child: ListTile(
              leading: Icon(Icons.qr_code_scanner),
              title: Text('Scan SKU'),
              subtitle: Text('// TODO Phase D'),
              enabled: false,
            ),
          ),
        ],
      ),
    );
  }
}

/// Queue card showing per-state count chips. Fatal states are tappable.
class _QueueCard extends StatelessWidget {
  final String queueName;
  final Map<String, int> states;
  final int stuckCount;
  final void Function(String state, String title) onStateTap;
  final VoidCallback onStuckTap;

  const _QueueCard({
    required this.queueName,
    required this.states,
    required this.stuckCount,
    required this.onStateTap,
    required this.onStuckTap,
  });

  @override
  Widget build(BuildContext context) {
    final chips = <Widget>[];

    void addChip(String state, int count) {
      if (count <= 0) return;
      final (color, tappable) = _stateStyle(state);
      chips.add(_StateChip(
        label: '$state:$count',
        color: color,
        onTap: tappable ? () => onStateTap(state, '$queueName — $state') : null,
      ));
    }

    for (final s in ['pending', 'running', 'leased', 'retry_wait', 'failed', 'dead_letter']) {
      addChip(s, states[s] ?? 0);
    }
    // catch any other states not in the ordered list
    for (final e in states.entries) {
      if (!['pending', 'running', 'leased', 'retry_wait', 'failed', 'dead_letter'].contains(e.key)) {
        addChip(e.key, e.value);
      }
    }
    if (stuckCount > 0) {
      chips.add(_StateChip(
        label: 'stuck:$stuckCount',
        color: Colors.yellow[700]!,
        onTap: onStuckTap,
      ));
    }

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(10.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              queueName,
              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
              overflow: TextOverflow.ellipsis,
              maxLines: 1,
            ),
            const SizedBox(height: 6),
            Expanded(
              child: Wrap(
                spacing: 4,
                runSpacing: 4,
                children: chips,
              ),
            ),
          ],
        ),
      ),
    );
  }

  (Color, bool) _stateStyle(String state) => switch (state) {
    'pending'     => (Colors.grey, false),
    'running'     => (Colors.blue, false),
    'leased'      => (Colors.blueGrey, false),
    'retry_wait'  => (Colors.orange, true),
    'failed'      => (Colors.deepOrange, true),
    'dead_letter' => (Colors.red, true),
    _             => (Colors.grey, false),
  };
}

class _StateChip extends StatelessWidget {
  final String label;
  final Color color;
  final VoidCallback? onTap;

  const _StateChip({required this.label, required this.color, this.onTap});

  @override
  Widget build(BuildContext context) {
    final chip = Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.2),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withValues(alpha: onTap != null ? 0.8 : 0.4)),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 10,
          color: color,
          fontWeight: onTap != null ? FontWeight.bold : FontWeight.normal,
        ),
      ),
    );

    if (onTap != null) {
      return GestureDetector(
        onTap: onTap,
        child: chip,
      );
    }
    return chip;
  }
}
