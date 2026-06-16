import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';

/// Bottom sheet showing pipeline jobs for a specific queue+state (or null for all stuck jobs).
class PipelineJobSheet extends ConsumerStatefulWidget {
  final String? queueName;
  final String? filterState; // null = stuck jobs across all queues
  final String title;

  const PipelineJobSheet({
    super.key,
    required this.title,
    this.queueName,
    this.filterState,
  });

  @override
  ConsumerState<PipelineJobSheet> createState() => _PipelineJobSheetState();
}

class _PipelineJobSheetState extends ConsumerState<PipelineJobSheet> {
  final Set<String> _busy = {};

  List<PipelineJob> _filter(List<PipelineJob> all) {
    if (widget.filterState == null) {
      // stuck mode
      return all.where((j) => j.isStuck).toList();
    }
    return all.where((j) {
      final qMatch = widget.queueName == null || j.queueName == widget.queueName;
      return qMatch && j.state == widget.filterState;
    }).toList();
  }

  Future<void> _requeue(PipelineJob job) async {
    setState(() => _busy.add(job.jobId));
    try {
      final api = ref.read(apiClientProvider);
      final r = await api.requeueJob(job.jobId);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(r.ok ? 'Re-queued as ${r.data ?? "new job"}' : 'Error: ${r.error}'),
        backgroundColor: r.ok ? Colors.green[800] : Colors.red[800],
      ));
      if (r.ok) ref.invalidate(pipelineJobsProvider);
    } finally {
      if (mounted) setState(() => _busy.remove(job.jobId));
    }
  }

  Future<void> _report(PipelineJob job) async {
    setState(() => _busy.add('r:${job.jobId}'));
    try {
      final api = ref.read(apiClientProvider);
      final r = await api.reportJobToAdmin(job.jobId, job.queueName, job.errorDetail);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(r.ok ? 'Reported to admin' : 'Error: ${r.error}'),
        backgroundColor: r.ok ? Colors.blue[800] : Colors.red[800],
      ));
    } finally {
      if (mounted) setState(() => _busy.remove('r:${job.jobId}'));
    }
  }

  @override
  Widget build(BuildContext context) {
    final jobsAsync = ref.watch(pipelineJobsProvider);

    return DraggableScrollableSheet(
      initialChildSize: 0.65,
      minChildSize: 0.3,
      maxChildSize: 0.95,
      expand: false,
      builder: (context, scrollController) {
        return Column(
          children: [
            _SheetHandle(title: widget.title),
            Expanded(
              child: jobsAsync.when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (err, _) => Center(child: Text('Error: $err')),
                data: (all) {
                  final jobs = _filter(all);
                  if (jobs.isEmpty) {
                    return const Center(
                      child: Padding(
                        padding: EdgeInsets.all(32),
                        child: Text('No jobs in this state.', style: TextStyle(color: Colors.grey)),
                      ),
                    );
                  }
                  return RefreshIndicator(
                    onRefresh: () => ref.refresh(pipelineJobsProvider.future),
                    child: ListView.separated(
                      controller: scrollController,
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      itemCount: jobs.length,
                      separatorBuilder: (_, __) => const Divider(height: 1),
                      itemBuilder: (ctx, i) => _JobTile(
                        job: jobs[i],
                        isBusy: _busy.contains(jobs[i].jobId),
                        isReportBusy: _busy.contains('r:${jobs[i].jobId}'),
                        onRequeue: _requeue,
                        onReport: _report,
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        );
      },
    );
  }
}

class _SheetHandle extends StatelessWidget {
  final String title;
  const _SheetHandle({required this.title});

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(height: 8),
          Container(
            width: 40,
            height: 4,
            decoration: BoxDecoration(
              color: Colors.grey[600],
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Row(
              children: [
                const Icon(Icons.work_history_outlined, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                ),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.pop(context),
                  padding: EdgeInsets.zero,
                  visualDensity: VisualDensity.compact,
                ),
              ],
            ),
          ),
          const Divider(height: 1),
        ],
      ),
    );
  }
}

class _JobTile extends StatelessWidget {
  final PipelineJob job;
  final bool isBusy;
  final bool isReportBusy;
  final Future<void> Function(PipelineJob) onRequeue;
  final Future<void> Function(PipelineJob) onReport;

  const _JobTile({
    required this.job,
    required this.isBusy,
    required this.isReportBusy,
    required this.onRequeue,
    required this.onReport,
  });

  @override
  Widget build(BuildContext context) {
    final label = job.sku ?? job.jobId.substring(0, job.jobId.length.clamp(0, 8));
    final errClass = job.errorClass;
    final elapsed = job.elapsed;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _ClassChip(errClass),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  label,
                  style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (job.isStuck)
                const Padding(
                  padding: EdgeInsets.only(left: 4),
                  child: Icon(Icons.warning_amber, size: 16, color: Colors.yellow),
                ),
              const SizedBox(width: 4),
              Text(
                job.queueName,
                style: const TextStyle(fontSize: 11, color: Colors.grey),
              ),
            ],
          ),
          if (elapsed != null) ...[
            const SizedBox(height: 2),
            Text(
              'Elapsed: ${_fmtDuration(elapsed)} · attempts ${job.attemptCount}/${job.maxAttempts}',
              style: const TextStyle(fontSize: 11, color: Colors.grey),
            ),
          ],
          if (job.errorDetail != null && job.errorDetail!.isNotEmpty) ...[
            const SizedBox(height: 4),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: Colors.red[950]?.withValues(alpha: 0.4) ?? Colors.red.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: Colors.red[900]!, width: 0.5),
              ),
              child: Text(
                job.errorDetail!,
                style: const TextStyle(fontSize: 11, fontFamily: 'monospace', color: Colors.redAccent),
                maxLines: 4,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
          const SizedBox(height: 6),
          Row(
            children: [
              if (job.state == 'dead_letter') ...[
                _ActionButton(
                  label: 'Re-queue',
                  icon: Icons.replay,
                  color: Colors.blue,
                  busy: isBusy,
                  onTap: () => onRequeue(job),
                ),
                const SizedBox(width: 8),
              ],
              _ActionButton(
                label: 'Report',
                icon: Icons.flag_outlined,
                color: Colors.orange,
                busy: isReportBusy,
                onTap: () => onReport(job),
              ),
            ],
          ),
        ],
      ),
    );
  }

  String _fmtDuration(Duration d) {
    if (d.inSeconds < 60) return '${d.inSeconds}s';
    if (d.inMinutes < 60) return '${d.inMinutes}m ${d.inSeconds % 60}s';
    return '${d.inHours}h ${d.inMinutes % 60}m';
  }
}

class _ClassChip extends StatelessWidget {
  final String errClass;
  const _ClassChip(this.errClass);

  @override
  Widget build(BuildContext context) {
    final (label, bg, fg) = switch (errClass) {
      'transient' => ('transient', Colors.orange[900]!, Colors.orange[200]!),
      'permanent' => ('permanent', Colors.red[900]!, Colors.red[200]!),
      _ => ('unknown', Colors.grey[800]!, Colors.grey[400]!),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(4)),
      child: Text(label, style: TextStyle(fontSize: 10, color: fg, fontWeight: FontWeight.bold)),
    );
  }
}

class _ActionButton extends StatelessWidget {
  final String label;
  final IconData icon;
  final Color color;
  final bool busy;
  final VoidCallback onTap;

  const _ActionButton({
    required this.label,
    required this.icon,
    required this.color,
    required this.busy,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 28,
      child: OutlinedButton.icon(
        onPressed: busy ? null : onTap,
        icon: busy
            ? SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 1.5, color: color))
            : Icon(icon, size: 14, color: color),
        label: Text(label, style: TextStyle(fontSize: 11, color: color)),
        style: OutlinedButton.styleFrom(
          padding: const EdgeInsets.symmetric(horizontal: 8),
          side: BorderSide(color: color.withValues(alpha: 0.5)),
          visualDensity: VisualDensity.compact,
        ),
      ),
    );
  }
}
