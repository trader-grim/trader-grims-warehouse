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

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _isLoading = true;
      _error = null;
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
      items = items
          .where(
            (i) =>
                i.title.toLowerCase().contains(q) ||
                i.sku.toLowerCase().contains(q),
          )
          .toList();
    }
    return items;
  }

  List<String> get _categories {
    final cats = _allItems
        .map((i) => i.categoryName)
        .where((c) => c.isNotEmpty)
        .toSet()
        .toList();
    cats.sort();
    return cats;
  }

  @override
  Widget build(BuildContext context) {
    final filtered = _filtered;
    return Column(
      children: [
        const _PurposeBanner(),
        _buildFilterBar(),
        Expanded(
          child: _isLoading
              ? const Center(child: CircularProgressIndicator())
              : _error != null
              ? Center(
                  child: Text(
                    'Error: $_error',
                    style: TextStyle(color: Colors.red[400]),
                  ),
                )
              : filtered.isEmpty
              ? _allItems.isEmpty
                    ? const _WorkflowGuideEmptyState()
                    : Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(
                              Icons.search_off,
                              size: 48,
                              color: Colors.grey,
                            ),
                            const SizedBox(height: 8),
                            Text(
                              'No items match filter',
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
                      return _ReviewCard(
                        item: item,
                        onTap: () => widget.onItemTap(item.sku),
                      );
                    },
                  ),
                ),
        ),
      ],
    );
  }

  Widget _buildFilterBar() {
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
                    final count = _allItems
                        .where((i) => i.categoryName == cat)
                        .length;
                    return Padding(
                      padding: const EdgeInsets.only(right: 6),
                      child: _FilterChip(
                        label: '$cat ($count)',
                        selected: _selectedCategory == cat,
                        onTap: () => setState(
                          () => _selectedCategory = _selectedCategory == cat
                              ? null
                              : cat,
                        ),
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
  final VoidCallback onTap;

  const _ReviewCard({required this.item, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final score = item.qualityScore;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
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
                            style: const TextStyle(
                              fontWeight: FontWeight.bold,
                              fontSize: 14,
                            ),
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
                              style: TextStyle(
                                fontSize: 11,
                                color: cs.onSurface.withAlpha(140),
                              ),
                            ),
                          const Spacer(),
                          Text(
                            item.sku,
                            style: TextStyle(
                              fontSize: 10,
                              color: cs.onSurface.withAlpha(100),
                            ),
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
    if (c.contains('excellent') || c.contains('like new')) {
      return Colors.lightGreen;
    }
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

  const _InfoChip({
    required this.icon,
    required this.label,
    required this.color,
  });

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
        style: TextStyle(
          fontSize: 11,
          color: _color,
          fontWeight: FontWeight.bold,
        ),
      ),
    );
  }
}

class _FilterChip extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _FilterChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: selected
              ? cs.primary.withAlpha(30)
              : cs.surfaceContainerHighest,
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
// Purpose banner — always shown at top of the Review tab
// ---------------------------------------------------------------------------

class _PurposeBanner extends StatelessWidget {
  const _PurposeBanner();

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      color: cs.primaryContainer.withAlpha(120),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.rate_review_outlined, size: 16, color: cs.primary),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Proposed changes from AI or operator review appear here before being pushed to eBay. (PP-REVISION-001)',
              style: TextStyle(fontSize: 12, color: cs.onPrimaryContainer),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Workflow guide — shown on empty state when queue has no items
// ---------------------------------------------------------------------------

class _WorkflowGuideEmptyState extends StatelessWidget {
  const _WorkflowGuideEmptyState();

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final textTheme = Theme.of(context).textTheme;
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
      child: Column(
        children: [
          const Icon(Icons.check_circle_outline, size: 56, color: Colors.green),
          const SizedBox(height: 12),
          Text('Review queue is empty', style: textTheme.titleMedium),
          const SizedBox(height: 24),
          Text(
            'How revisions work',
            style: textTheme.labelLarge?.copyWith(
              color: cs.onSurface.withAlpha(160),
            ),
          ),
          const SizedBox(height: 16),
          const _WorkflowStep(
            step: 1,
            icon: Icons.smart_toy_outlined,
            title: 'AI proposes changes',
            description:
                'The ai_identify or ebay_draft worker analyses the item and proposes field updates (title, condition, aspects, price).',
            color: Colors.indigo,
          ),
          const SizedBox(height: 12),
          const _WorkflowStep(
            step: 2,
            icon: Icons.difference_outlined,
            title: 'Review the diff',
            description:
                'Items with pending proposals appear here. Inspect the suggested changes, approve or reject them.',
            color: Colors.orange,
          ),
          const SizedBox(height: 12),
          const _WorkflowStep(
            step: 3,
            icon: Icons.cloud_upload_outlined,
            title: 'Apply pushes to eBay',
            description:
                'Approved changes are applied to the item record and queued for the ebay_upload / ebay_price workers.',
            color: Colors.teal,
          ),
        ],
      ),
    );
  }
}

class _WorkflowStep extends StatelessWidget {
  final int step;
  final IconData icon;
  final String title;
  final String description;
  final Color color;

  const _WorkflowStep({
    required this.step,
    required this.icon,
    required this.title,
    required this.description,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 32,
          height: 32,
          decoration: BoxDecoration(
            color: color.withAlpha(30),
            shape: BoxShape.circle,
            border: Border.all(color: color.withAlpha(120)),
          ),
          alignment: Alignment.center,
          child: Text(
            '$step',
            style: TextStyle(
              fontWeight: FontWeight.bold,
              color: color,
              fontSize: 14,
            ),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(icon, size: 16, color: color),
                  const SizedBox(width: 6),
                  Text(
                    title,
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 13,
                      color: cs.onSurface,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                description,
                style: TextStyle(
                  fontSize: 12,
                  color: cs.onSurface.withAlpha(160),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
