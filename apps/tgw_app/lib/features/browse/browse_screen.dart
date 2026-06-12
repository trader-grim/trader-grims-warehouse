import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:cached_network_image/cached_network_image.dart';
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

  @override
  void initState() {
    super.initState();
    _loadMore();
    _scrollController.addListener(() {
      if (_scrollController.position.pixels >= _scrollController.position.maxScrollExtent - 200) {
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
                    gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
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
                      return _ItemCard(
                        item: item,
                        onTap: () => widget.onItemTap(item.sku),
                        thumbnailUrl: ref.read(apiClientProvider).getThumbnailUrl(item.sku),
                        isOnline: ref.watch(connectionStatusProvider) == ConnectionStatus.online,
                        localPath: ref.read(offlineDbProvider).getLocalThumbnailPath(item.sku),
                      );
                    },
                  ),
          ),
        ),
      ],
    );
  }

  Widget _buildFilters() {
    return Padding(
      padding: const EdgeInsets.all(8.0),
      child: Column(
        children: [
          TextField(
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
                        contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 0),
                        border: OutlineInputBorder(),
                      ),
                      initialValue: _selectedLocation,
                      items: [
                        const DropdownMenuItem(value: null, child: Text('All')),
                        ...?(snapshot.data?.map((l) => DropdownMenuItem(value: l, child: Text(l)))),
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
                    contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 0),
                    border: OutlineInputBorder(),
                  ),
                  initialValue: _selectedStatus,
                  items: const [
                    DropdownMenuItem(value: null, child: Text('All')),
                    DropdownMenuItem(value: 'In Stock', child: Text('In Stock')),
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

class _ItemCard extends StatelessWidget {
  final ItemSummary item;
  final VoidCallback onTap;
  final String thumbnailUrl;
  final bool isOnline;
  final String? localPath;

  const _ItemCard({
    required this.item,
    required this.onTap,
    required this.thumbnailUrl,
    required this.isOnline,
    this.localPath,
  });

  @override
  Widget build(BuildContext context) {
    Widget image;
    if (isOnline) {
      image = CachedNetworkImage(
        imageUrl: thumbnailUrl,
        fit: BoxFit.cover,
        width: double.infinity,
        placeholder: (context, url) => Container(color: Colors.grey[200]),
        errorWidget: (context, url, error) => const Icon(Icons.broken_image),
      );
    } else if (localPath != null && File(localPath!).existsSync()) {
      image = Image.file(
        File(localPath!),
        fit: BoxFit.cover,
        width: double.infinity,
        errorBuilder: (context, error, stackTrace) => const Icon(Icons.broken_image),
      );
    } else {
      image = const Center(child: Icon(Icons.offline_pin, color: Colors.grey));
    }

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: image),
            Padding(
              padding: const EdgeInsets.all(8.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    item.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Chip(
                        label: Text(item.location, style: const TextStyle(fontSize: 10)),
                        padding: EdgeInsets.zero,
                        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      ),
                      Text('\$${item.price}', style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.blue)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  _StatusBadge(status: item.status),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatusBadge extends StatelessWidget {
  final String status;
  const _StatusBadge({required this.status});

  @override
  Widget build(BuildContext context) {
    Color color;
    switch (status) {
      case 'In Stock': color = Colors.blue; break;
      case 'Draft': color = Colors.orange; break;
      case 'Staged': color = Colors.purple; break;
      case 'Active': color = Colors.green; break;
      case 'Sold': color = Colors.grey; break;
      default: color = Colors.grey;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withAlpha(25),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color),
      ),
      child: Text(
        status,
        style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.bold),
      ),
    );
  }
}
