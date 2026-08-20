import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../providers/providers.dart';
import '../../models/models.dart';

class BrowseScreen extends ConsumerStatefulWidget {
  final Function(String) onItemTap;
  const BrowseScreen({super.key, required this.onItemTap});

  @override
  ConsumerState<BrowseScreen> createState() => _BrowseScreenState();
}

class _BrowseScreenState extends ConsumerState<BrowseScreen> {
  final List<ItemSummary> _items = [];
  bool _isLoading = false;
  bool _hasMore = true;
  int _offset = 0;
  final int _limit = 50;

  String _search = '';
  String? _selectedLocation;
  String? _selectedStatus;

  final ScrollController _scrollController = ScrollController();

  // Selection state
  final Set<String> _selectedSkus = {};
  bool get _selectionMode => _selectedSkus.isNotEmpty;

  @override
  void initState() {
    super.initState();
    _loadMore();
    _scrollController.addListener(() {
      if (_scrollController.position.pixels >=
          _scrollController.position.maxScrollExtent - 200) {
        _loadMore();
      }
    });
  }

  Future<void> _loadMore({bool refresh = false}) async {
    if (_isLoading || (!_hasMore && !refresh)) return;

    setState(() {
      _isLoading = true;
      if (refresh) {
        _items.clear();
        _offset = 0;
        _hasMore = true;
        _selectedSkus.clear();
      }
    });

    try {
      final repo = ref.read(repositoryProvider);
      final newItems = await repo.getItems(
        search: _search,
        location: _selectedLocation,
        statusFilter: _selectedStatus,
        limit: _limit,
        offset: _offset,
      );

      setState(() {
        _items.addAll(newItems);
        _offset += newItems.length;
        _hasMore = newItems.length == _limit;
        _isLoading = false;
      });
    } catch (e) {
      setState(() => _isLoading = false);
    }
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
    setState(() {
      if (_selectedSkus.length == _items.length) {
        _selectedSkus.clear();
      } else {
        _selectedSkus.addAll(_items.map((i) => i.sku));
      }
    });
  }

  void _clearSelection() => setState(() => _selectedSkus.clear());

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
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('Cancel')),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Confirm'),
            ),
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

      final ok = result != null && (result['ok'] == true);
      final count =
          result?['count'] ?? result?['marked']?.length ?? skus.length;
      final errors = (result?['errors'] as List?)?.cast<String>() ?? [];

      String msg;
      if (ok || errors.isEmpty) {
        msg = '$action: $count item(s) done.';
      } else {
        msg =
            '$action: $count done, ${errors.length} error(s): ${errors.take(3).join('; ')}';
      }

      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(
          content: Text(msg),
          backgroundColor: ok ? null : Colors.red[700],
          duration: const Duration(seconds: 4),
        ));

      if (action == 'mark_sold' || action == 'delete') {
        _loadMore(refresh: true);
      } else {
        _clearSelection();
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(
            content: Text('Error: $e'), backgroundColor: Colors.red[700]));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _buildFilters(),
        Expanded(
          child: RefreshIndicator(
            onRefresh: () => _loadMore(refresh: true),
            child: _items.isEmpty && !_isLoading
                ? const Center(child: Text('No items found'))
                : GridView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.all(8.0),
                    gridDelegate:
                        const SliverGridDelegateWithMaxCrossAxisExtent(
                      maxCrossAxisExtent: 250,
                      mainAxisSpacing: 8,
                      crossAxisSpacing: 8,
                      childAspectRatio: 0.75,
                    ),
                    itemCount: _items.length + (_hasMore ? 1 : 0),
                    itemBuilder: (context, index) {
                      if (index == _items.length) {
                        return const Center(child: CircularProgressIndicator());
                      }
                      final item = _items[index];
                      final isSelected = _selectedSkus.contains(item.sku);
                      return _ItemCard(
                        item: item,
                        onTap: _selectionMode
                            ? () => _toggleSelection(item.sku)
                            : () => widget.onItemTap(item.sku),
                        onLongPress: () => _toggleSelection(item.sku),
                        isSelected: isSelected,
                        thumbnailUrl: ref
                            .read(apiClientProvider)
                            .getThumbnailUrl(item.sku),
                        isOnline: ref.watch(connectionStatusProvider) ==
                            ConnectionStatus.online,
                        localPath: ref
                            .read(offlineDbProvider)
                            .getLocalThumbnailPath(item.sku),
                      );
                    },
                  ),
          ),
        ),
        if (_selectionMode)
          _BulkToolbar(
            selectedCount: _selectedSkus.length,
            onClear: _clearSelection,
            onAction: _runBulkAction,
          ),
      ],
    );
  }

  Widget _buildFilters() {
    final allSelected =
        _items.isNotEmpty && _selectedSkus.length == _items.length;
    return Padding(
      padding: const EdgeInsets.all(8.0),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: TextField(
                  decoration: const InputDecoration(
                    hintText: 'Search title or SKU...',
                    prefixIcon: Icon(Icons.search),
                    border: OutlineInputBorder(),
                    contentPadding: EdgeInsets.symmetric(vertical: 0),
                  ),
                  onChanged: (val) {
                    _search = val;
                    _loadMore(refresh: true);
                  },
                ),
              ),
              const SizedBox(width: 8),
              Tooltip(
                message: allSelected
                    ? 'Deselect all'
                    : 'Select all (${_items.length})',
                child: OutlinedButton.icon(
                  onPressed: _items.isEmpty ? null : _selectAll,
                  icon: Icon(
                    allSelected ? Icons.deselect : Icons.select_all,
                    size: 18,
                  ),
                  label: Text(
                    _selectedSkus.isEmpty
                        ? 'Select'
                        : '${_selectedSkus.length}/${_items.length}',
                    style: const TextStyle(fontSize: 12),
                  ),
                  style: OutlinedButton.styleFrom(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 0),
                    minimumSize: const Size(0, 36),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: FutureBuilder<List<String>>(
                  future: ref.read(repositoryProvider).getLocations(),
                  builder: (context, snapshot) {
                    return DropdownButtonFormField<String>(
                      decoration: const InputDecoration(
                        labelText: 'Location',
                        contentPadding:
                            EdgeInsets.symmetric(horizontal: 10, vertical: 0),
                        border: OutlineInputBorder(),
                      ),
                      initialValue: _selectedLocation,
                      items: [
                        const DropdownMenuItem(value: null, child: Text('All')),
                        ...?(snapshot.data?.map(
                            (l) => DropdownMenuItem(value: l, child: Text(l)))),
                      ],
                      onChanged: (val) {
                        setState(() => _selectedLocation = val);
                        _loadMore(refresh: true);
                      },
                    );
                  },
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: DropdownButtonFormField<String>(
                  decoration: const InputDecoration(
                    labelText: 'Status',
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 10, vertical: 0),
                    border: OutlineInputBorder(),
                  ),
                  initialValue: _selectedStatus,
                  items: const [
                    DropdownMenuItem(value: null, child: Text('All')),
                    DropdownMenuItem(
                        value: 'In Stock', child: Text('In Stock')),
                    DropdownMenuItem(value: 'Draft', child: Text('Draft')),
                    DropdownMenuItem(value: 'Staged', child: Text('Staged')),
                    DropdownMenuItem(value: 'Active', child: Text('Active')),
                    DropdownMenuItem(value: 'Sold', child: Text('Sold')),
                  ],
                  onChanged: (val) {
                    setState(() => _selectedStatus = val);
                    _loadMore(refresh: true);
                  },
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Bulk action toolbar — appears when items are selected
// ---------------------------------------------------------------------------

class _BulkToolbar extends StatelessWidget {
  final int selectedCount;
  final VoidCallback onClear;
  final Future<void> Function(String action, {String? confirmMessage}) onAction;

  const _BulkToolbar({
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
                  Text(
                    '$selectedCount selected',
                    style: Theme.of(context).textTheme.labelLarge,
                  ),
                  const Spacer(),
                ],
              ),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    _ActionChip(
                      icon: Icons.search,
                      label: 'Re-identify',
                      color: Colors.indigo,
                      onTap: () => onAction('ai_identify'),
                    ),
                    const SizedBox(width: 6),
                    _ActionChip(
                      icon: Icons.attach_money,
                      label: 'Reprice',
                      color: Colors.teal,
                      onTap: () => onAction('ebay_price'),
                    ),
                    const SizedBox(width: 6),
                    _ActionChip(
                      icon: Icons.check_circle_outline,
                      label: 'Mark Ready',
                      color: Colors.green,
                      onTap: () => onAction('set_ready'),
                    ),
                    const SizedBox(width: 6),
                    _ActionChip(
                      icon: Icons.sell_outlined,
                      label: 'Mark Sold',
                      color: Colors.orange,
                      onTap: () => onAction(
                        'mark_sold',
                        confirmMessage: 'Mark {n} item(s) as Sold?',
                      ),
                    ),
                    const SizedBox(width: 6),
                    _ActionChip(
                      icon: Icons.delete_outline,
                      label: 'Delete',
                      color: Colors.red,
                      onTap: () => onAction(
                        'delete',
                        confirmMessage:
                            'Delete {n} item(s) locally? This does not end eBay listings.',
                      ),
                    ),
                    const SizedBox(width: 6),
                    _ActionChip(
                      icon: Icons.auto_fix_high,
                      label: 'Apply Draft',
                      color: Colors.purple,
                      onTap: () => onAction('ebay_draft'),
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

// ---------------------------------------------------------------------------
// Item card
// ---------------------------------------------------------------------------

class _ItemCard extends StatelessWidget {
  final ItemSummary item;
  final VoidCallback onTap;
  final VoidCallback onLongPress;
  final bool isSelected;
  final String thumbnailUrl;
  final bool isOnline;
  final String? localPath;

  const _ItemCard({
    required this.item,
    required this.onTap,
    required this.onLongPress,
    required this.isSelected,
    required this.thumbnailUrl,
    required this.isOnline,
    this.localPath,
  });

  bool get _missingPhoto => item.image == null || item.image!.isEmpty;

  @override
  Widget build(BuildContext context) {
    Widget imageWidget;
    if (isOnline) {
      imageWidget = CachedNetworkImage(
        imageUrl: thumbnailUrl,
        fit: BoxFit.cover,
        width: double.infinity,
        placeholder: (context, url) => Container(color: Colors.grey[200]),
        errorWidget: (context, url, error) => const Icon(Icons.broken_image),
      );
    } else if (localPath != null && File(localPath!).existsSync()) {
      imageWidget = Image.file(
        File(localPath!),
        fit: BoxFit.cover,
        width: double.infinity,
        errorBuilder: (context, error, stackTrace) =>
            const Icon(Icons.broken_image),
      );
    } else {
      imageWidget =
          const Center(child: Icon(Icons.offline_pin, color: Colors.grey));
    }

    final String priceText = item.price.isNotEmpty ? '\$${item.price}' : '—';
    final cs = Theme.of(context).colorScheme;

    return Card(
      clipBehavior: Clip.antiAlias,
      shape: isSelected
          ? RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: BorderSide(color: cs.primary, width: 2),
            )
          : null,
      child: InkWell(
        onTap: onTap,
        onLongPress: onLongPress,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Stack(
                fit: StackFit.expand,
                children: [
                  imageWidget,
                  if (isSelected)
                    Container(
                      color: cs.primary.withAlpha(40),
                      alignment: Alignment.topLeft,
                      padding: const EdgeInsets.all(4),
                      child:
                          Icon(Icons.check_circle, color: cs.primary, size: 22),
                    )
                  else
                    Positioned(
                      top: 4,
                      left: 4,
                      child: Container(
                        width: 22,
                        height: 22,
                        decoration: const BoxDecoration(
                          color: Colors.black38,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(Icons.circle_outlined,
                            size: 16, color: Colors.white70),
                      ),
                    ),
                  if (_missingPhoto)
                    Positioned(
                      top: 4,
                      right: 4,
                      child: Container(
                        padding: const EdgeInsets.all(2),
                        decoration: BoxDecoration(
                          color: Colors.black54,
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: const Icon(Icons.photo_camera_outlined,
                            size: 14, color: Colors.amber),
                      ),
                    ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(8.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    item.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontWeight: FontWeight.bold, fontSize: 12),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Flexible(
                        child: Chip(
                          label: Text(item.location,
                              style: const TextStyle(fontSize: 10)),
                          padding: EdgeInsets.zero,
                          materialTapTargetSize:
                              MaterialTapTargetSize.shrinkWrap,
                        ),
                      ),
                      Text(priceText,
                          style: const TextStyle(
                              fontWeight: FontWeight.bold,
                              color: Colors.blue,
                              fontSize: 12)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  _EbayStatusBadge(item: item),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

enum _EbayState { listed, ready, staged, needsReview, notListed }

class _EbayStatusBadge extends StatelessWidget {
  final ItemSummary item;
  const _EbayStatusBadge({required this.item});

  _EbayState get _state {
    if (item.status.toLowerCase() == 'sold') return _EbayState.notListed;
    final lid = item.ebayListingId;
    if (lid != null && lid.isNotEmpty) return _EbayState.listed;
    final oid = item.ebayOfferId;
    if (oid != null && oid.isNotEmpty) {
      final rat = item.ebayReadyAt;
      return (rat != null && rat.isNotEmpty)
          ? _EbayState.ready
          : _EbayState.staged;
    }
    if (item.hasDraft) return _EbayState.needsReview;
    return _EbayState.notListed;
  }

  @override
  Widget build(BuildContext context) {
    final state = _state;
    late Color color;
    late String label;

    switch (state) {
      case _EbayState.listed:
        color = Colors.green;
        label = 'Listed · ${item.ebayListingId!}';
      case _EbayState.ready:
        color = Colors.teal;
        label = 'Ready';
      case _EbayState.staged:
        color = Colors.purple;
        label = 'Staged';
      case _EbayState.needsReview:
        color = Colors.orange;
        label = 'Needs Review';
      case _EbayState.notListed:
        color = Colors.grey;
        label = 'Not Listed';
    }

    final badge = Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withAlpha(25),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color),
      ),
      child: Text(
        label,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style:
            TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.bold),
      ),
    );

    if (state == _EbayState.listed) {
      return GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () async {
          final url =
              Uri.parse('https://www.ebay.com/itm/${item.ebayListingId}');
          if (await canLaunchUrl(url)) {
            await launchUrl(url, mode: LaunchMode.externalApplication);
          }
        },
        child: badge,
      );
    }
    return badge;
  }
}
