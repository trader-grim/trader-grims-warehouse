import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';

class ReviewQueueScreen extends ConsumerStatefulWidget {
  final Function(String) onItemTap;
  const ReviewQueueScreen({super.key, required this.onItemTap});

  @override
  ConsumerState<ReviewQueueScreen> createState() => _ReviewQueueScreenState();
}

class _ReviewQueueScreenState extends ConsumerState<ReviewQueueScreen> {
  List<ReviewQueueItem> _allItems = [];
  bool _isLoading = false;
  String? _error;

  String _search = '';
  String? _selectedCategory;

  final Set<String> _selectedSkus = {};
  bool get _selectionMode => _selectedSkus.isNotEmpty;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _isLoading = true;
      _error = null;
      _selectedSkus.clear();
    });
    try {
      final items = await ref.read(repositoryProvider).getReviewQueue();
      setState(() {
        _allItems = items;
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _isLoading = false;
      });
    }
  }

  List<ReviewQueueItem> get _filtered {
    var items = _allItems;
    if (_selectedCategory != null) {
      items = items.where((i) => i.categoryName == _selectedCategory).toList();
    }
    if (_search.isNotEmpty) {
      final q = _search.toLowerCase();
      items = items.where((i) =>
        i.title.toLowerCase().contains(q) || i.sku.toLowerCase().contains(q)).toList();
    }
    return items;
  }

  List<String> get _categories {
    final cats = _allItems.map((i) => i.categoryName).where((c) => c.isNotEmpty).toSet().toList();
    cats.sort();
    return cats;
  }

  void _toggleSelection(String sku) {
    setState(() {
      if (_selectedSkus.contains(sku)) {
        _selectedSkus.remove(sku);
      } else {
        _selectedSkus.add(sku);
      }
    });
  }

  void _selectAll() {
    final visible = _filtered.map((i) => i.sku).toSet();
    setState(() {
      if (_selectedSkus.containsAll(visible) && _selectedSkus.length == visible.length) {
        _selectedSkus.clear();
      } else {
        _selectedSkus.addAll(visible);
      }
    });
  }

  Future<void> _runBulkAction(String action, {String? confirmMessage}) async {
    final skus = _selectedSkus.toList();
    if (skus.isEmpty) return;

    if (confirmMessage != null) {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Confirm'),
          content: Text(confirmMessage.replaceAll('{n}', '${skus.length}')),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Confirm')),
          ],
        ),
      );
      if (confirmed != true) return;
    }

    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Running $action on ${skus.length} item(s)…')),
    );

    try {
      final repo = ref.read(repositoryProvider);
      final result = await repo.bulkAction(skus, action);
      if (!mounted) return;

      final ok = result != null && result['ok'] == true;
      final count = result?['count'] ?? skus.length;
      final errors = (result?['errors'] as List?)?.cast<String>() ?? [];
      final msg = ok
          ? '$action: $count item(s) done.'
          : '$action: $count done, ${errors.length} error(s): ${errors.take(3).join('; ')}';

      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(
          content: Text(msg),
          backgroundColor: ok ? null : Colors.red[700],
          duration: const Duration(seconds: 4),
        ));

      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text('Error: $e'), backgroundColor: Colors.red[700]));
    }
  }

  @override
  Widget build(BuildContext context) {
    final filtered = _filtered;
    final allVisibleSelected = filtered.isNotEmpty &&
        filtered.every((i) => _selectedSkus.contains(i.sku));

    return Column(
      children: [
        _buildFilterBar(allVisibleSelected, filtered.length),
        Expanded(
          child: _isLoading
              ? const Center(child: CircularProgressIndicator())
              : _error != null
                  ? Center(child: Text('Error: $_error', style: TextStyle(color: Colors.red[400])))
                  : filtered.isEmpty
                      ? Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Icon(Icons.check_circle_outline, size: 48, color: Colors.green),
                              const SizedBox(height: 8),
                              Text(
                                _allItems.isEmpty ? 'Review queue is empty' : 'No items match filter',
                                style: Theme.of(context).textTheme.bodyLarge,
                              ),
                            ],
                          ),
                        )
                      : RefreshIndicator(
                          onRefresh: _load,
                          child: ListView.builder(
                            padding: const EdgeInsets.all(8),
                            itemCount: filtered.length,
                            itemBuilder: (context, index) {
                              final item = filtered[index];
                              final selected = _selectedSkus.contains(item.sku);
                              return _ReviewCard(
                                item: item,
                                isSelected: selected,
                                onTap: _selectionMode
                                    ? () => _toggleSelection(item.sku)
                                    : () => widget.onItemTap(item.sku),
                                onLongPress: () => _toggleSelection(item.sku),
                                onCheckChanged: (_) => _toggleSelection(item.sku),
                              );
                            },
                          ),
                        ),
        ),
        if (_selectionMode)
          _BulkReviewToolbar(
            selectedCount: _selectedSkus.length,
            onClear: () => setState(() => _selectedSkus.clear()),
            onAction: _runBulkAction,
          ),
      ],
    );
  }

  Widget _buildFilterBar(bool allSelected, int visibleCount) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 4),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: TextField(
                  decoration: const InputDecoration(
                    hintText: 'Search title or SKU…',
                    prefixIcon: Icon(Icons.search),
                    border: OutlineInputBorder(),
                    contentPadding: EdgeInsets.symmetric(vertical: 0),
                    isDense: true,
                  ),
                  onChanged: (v) => setState(() => _search = v),
                ),
              ),
              const SizedBox(width: 8),
              Tooltip(
                message: allSelected ? 'Deselect all' : 'Select all ($visibleCount)',
                child: OutlinedButton.icon(
                  onPressed: _filtered.isEmpty ? null : _selectAll,
                  icon: Icon(
                    allSelected ? Icons.deselect : Icons.select_all,
                    size: 18,
                  ),
                  label: Text(
                    _selectedSkus.isEmpty ? 'Select' : '${_selectedSkus.length}/$visibleCount',
                    style: const TextStyle(fontSize: 12),
                  ),
                  style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 0),
                    minimumSize: const Size(0, 36),
                  ),
                ),
              ),
            ],
          ),
          if (_categories.length > 1) ...[
            const SizedBox(height: 6),
            SizedBox(
              height: 32,
              child: ListView(
                scrollDirection: Axis.horizontal,
                children: [
                  _FilterChip(
                    label: 'All (${_allItems.length})',
                    selected: _selectedCategory == null,
                    onTap: () => setState(() => _selectedCategory = null),
                  ),
                  const SizedBox(width: 6),
                  ..._categories.map((cat) {
                    final count = _allItems.where((i) => i.categoryName == cat).length;
                    return Padding(
                      padding: const EdgeInsets.only(right: 6),
                      child: _FilterChip(
                        label: '$cat ($count)',
                        selected: _selectedCategory == cat,
                        onTap: () => setState(() =>
                            _selectedCategory = _selectedCategory == cat ? null : cat),
                      ),
                    );
                  }),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Review card — shows all detail fields for operator review
// ---------------------------------------------------------------------------

class _ReviewCard extends StatelessWidget {
  final ReviewQueueItem item;
  final bool isSelected;
  final VoidCallback onTap;
  final VoidCallback onLongPress;
  final ValueChanged<bool?> onCheckChanged;

  const _ReviewCard({
    required this.item,
    required this.isSelected,
    required this.onTap,
    required this.onLongPress,
    required this.onCheckChanged,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final score = item.qualityScore;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      shape: isSelected
          ? RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: BorderSide(color: cs.primary, width: 2),
            )
          : null,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        onLongPress: onLongPress,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Checkbox(
                value: isSelected,
                onChanged: onCheckChanged,
                visualDensity: VisualDensity.compact,
              ),
              const SizedBox(width: 4),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Title + price row
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: Text(
                            item.title,
                            style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        const SizedBox(width: 8),
                        if (item.price != null)
                          Text(
                            '\$${item.price!.toStringAsFixed(2)}',
                            style: const TextStyle(
                              fontWeight: FontWeight.bold,
                              color: Colors.blue,
                              fontSize: 14,
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    // Condition + category row
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: [
                        if (item.conditionLabel.isNotEmpty)
                          _InfoChip(
                            icon: Icons.star_border,
                            label: item.conditionLabel,
                            color: _conditionColor(item.conditionLabel),
                          )
                        else if (item.condition.isNotEmpty)
                          _InfoChip(
                            icon: Icons.star_border,
                            label: item.condition,
                            color: _conditionColor(item.condition),
                          ),
                        if (item.categoryName.isNotEmpty)
                          _InfoChip(
                            icon: Icons.category_outlined,
                            label: item.categoryName,
                            color: Colors.indigo,
                          ),
                        if (item.shippingProfile.isNotEmpty)
                          _InfoChip(
                            icon: Icons.local_shipping_outlined,
                            label: item.shippingProfile,
                            color: Colors.teal,
                          ),
                        if (item.location.isNotEmpty)
                          _InfoChip(
                            icon: Icons.place_outlined,
                            label: item.location,
                            color: Colors.grey,
                          ),
                      ],
                    ),
                    // Condition description
                    if (item.conditionDescription.isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Text(
                        item.conditionDescription,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 12,
                          color: cs.onSurface.withAlpha(160),
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    ],
                    // Quality + aspects row
                    if (score != null || item.aspectsSummary.isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Row(
                        children: [
                          if (score != null) ...[
                            _QualityBadge(score: score),
                            const SizedBox(width: 8),
                          ],
                          if (item.aspectsSummary.isNotEmpty)
                            Text(
                              item.aspectsSummary,
                              style: TextStyle(fontSize: 11, color: cs.onSurface.withAlpha(140)),
                            ),
                          const Spacer(),
                          Text(
                            item.sku,
                            style: TextStyle(fontSize: 10, color: cs.onSurface.withAlpha(100)),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Color _conditionColor(String condition) {
    final c = condition.toLowerCase();
    if (c.contains('new') || c.contains('brand')) return Colors.green;
    if (c.contains('excellent') || c.contains('like new')) return Colors.lightGreen;
    if (c.contains('good')) return Colors.blue;
    if (c.contains('acceptable') || c.contains('fair')) return Colors.orange;
    if (c.contains('poor') || c.contains('damaged')) return Colors.red;
    return Colors.grey;
  }
}

class _InfoChip extends StatelessWidget {
  final IconData icon;
  final String label;
  final Color color;

  const _InfoChip({required this.icon, required this.label, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withAlpha(20),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withAlpha(80)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 11, color: color),
          const SizedBox(width: 3),
          Text(label, style: TextStyle(fontSize: 11, color: color)),
        ],
      ),
    );
  }
}

class _QualityBadge extends StatelessWidget {
  final int score;
  const _QualityBadge({required this.score});

  Color get _color {
    if (score >= 80) return Colors.green;
    if (score >= 60) return Colors.orange;
    return Colors.red;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: _color.withAlpha(25),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: _color),
      ),
      child: Text(
        'Q$score',
        style: TextStyle(fontSize: 11, color: _color, fontWeight: FontWeight.bold),
      ),
    );
  }
}

class _FilterChip extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _FilterChip({required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: selected ? cs.primary.withAlpha(30) : cs.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: selected ? cs.primary : Colors.transparent),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 12,
            color: selected ? cs.primary : cs.onSurface,
            fontWeight: selected ? FontWeight.bold : FontWeight.normal,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Bulk action toolbar for review queue
// ---------------------------------------------------------------------------

class _BulkReviewToolbar extends StatelessWidget {
  final int selectedCount;
  final VoidCallback onClear;
  final Future<void> Function(String action, {String? confirmMessage}) onAction;

  const _BulkReviewToolbar({
    required this.selectedCount,
    required this.onClear,
    required this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Material(
      elevation: 8,
      color: cs.surfaceContainerHighest,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  IconButton(
                    onPressed: onClear,
                    icon: const Icon(Icons.close),
                    tooltip: 'Clear selection',
                    iconSize: 20,
                  ),
                  Text('$selectedCount selected',
                      style: Theme.of(context).textTheme.labelLarge),
                  const Spacer(),
                ],
              ),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    _ActionChip(
                      icon: Icons.check_circle_outline,
                      label: 'Approve',
                      color: Colors.green,
                      onTap: () => onAction(
                        'approve',
                        confirmMessage: 'Approve {n} item(s) for listing?',
                      ),
                    ),
                    const SizedBox(width: 6),
                    _ActionChip(
                      icon: Icons.rocket_launch_outlined,
                      label: 'List Now',
                      color: Colors.blue,
                      onTap: () => onAction(
                        'list_now',
                        confirmMessage: 'Immediately stage & queue {n} item(s) for listing?',
                      ),
                    ),
                    const SizedBox(width: 6),
                    _ActionChip(
                      icon: Icons.playlist_add_check,
                      label: 'Mark Ready',
                      color: Colors.teal,
                      onTap: () => onAction(
                        'approve',
                        confirmMessage: 'Mark {n} item(s) as Ready?',
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ActionChip extends StatelessWidget {
  final IconData icon;
  final String label;
  final Color color;
  final VoidCallback onTap;

  const _ActionChip({
    required this.icon,
    required this.label,
    required this.color,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return ActionChip(
      avatar: Icon(icon, size: 16, color: color),
      label: Text(label, style: TextStyle(color: color, fontSize: 12)),
      side: BorderSide(color: color.withAlpha(100)),
      backgroundColor: color.withAlpha(20),
      onPressed: onTap,
      padding: const EdgeInsets.symmetric(horizontal: 4),
      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
    );
  }
}
