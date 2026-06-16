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
        errorBuilder: (context, error, stackTrace) => const Icon(Icons.broken_image),
      );
    } else {
      imageWidget = const Center(child: Icon(Icons.offline_pin, color: Colors.grey));
    }

    final String priceText = item.price.isNotEmpty ? '\$${item.price}' : '—';

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Stack(
                fit: StackFit.expand,
                children: [
                  imageWidget,
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
                        child: const Icon(Icons.photo_camera_outlined, size: 14, color: Colors.amber),
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
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Flexible(
                        child: Chip(
                          label: Text(item.location, style: const TextStyle(fontSize: 10)),
                          padding: EdgeInsets.zero,
                          materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                        ),
                      ),
                      Text(priceText, style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.blue, fontSize: 12)),
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
      return (rat != null && rat.isNotEmpty) ? _EbayState.ready : _EbayState.staged;
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
        style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.bold),
      ),
    );

    if (state == _EbayState.listed) {
      return GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () async {
          final url = Uri.parse('https://www.ebay.com/itm/${item.ebayListingId}');
          if (await canLaunchUrl(url)) await launchUrl(url, mode: LaunchMode.externalApplication);
        },
        child: badge,
      );
    }
    return badge;
  }
}
