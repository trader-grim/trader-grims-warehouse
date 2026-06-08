import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/providers.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final queueStatusAsync = ref.watch(queueStatusProvider);

    return RefreshIndicator(
      onRefresh: () => ref.refresh(queueStatusProvider.future),
      child: ListView(
        padding: const EdgeInsets.all(16.0),
        children: [
          const Text(
            'Queue Status',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
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
                  maxCrossAxisExtent: 200,
                  mainAxisSpacing: 10,
                  crossAxisSpacing: 10,
                  childAspectRatio: 1.5,
                ),
                itemCount: status.queues.length,
                itemBuilder: (context, index) {
                  final queueName = status.queues.keys.elementAt(index);
                  final states = status.queues[queueName]!;
                  final total = states.values.fold(0, (sum, count) => sum + count);
                  final pending = states['pending'] ?? 0;

                  return Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12.0),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            queueName,
                            style: const TextStyle(fontWeight: FontWeight.bold),
                            overflow: TextOverflow.ellipsis,
                          ),
                          const Spacer(),
                          Text('Total: $total', style: const TextStyle(fontSize: 12)),
                          Text(
                            'Pending: $pending',
                            style: TextStyle(
                              fontSize: 14,
                              color: pending > 0 ? Colors.orange : Colors.green,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
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
          const Text(
            'Quick Actions',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          ),
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
