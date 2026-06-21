import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../../providers/providers.dart';
import '../../models/models.dart';
import 'edit_item_screen.dart';

class ItemScreen extends ConsumerWidget {
  final String? sku;
  const ItemScreen({super.key, this.sku});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (sku == null) {
      return const Center(child: Text('Select an item from Browse tab'));
    }

    final itemAsync = ref.watch(itemDetailProvider(sku!));

    return itemAsync.when(
      data: (item) {
        if (item == null) return const Center(child: Text('Item not found'));
        return _ItemDetailView(item: item);
      },
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (err, stack) => Center(child: Text('Error: $err')),
    );
  }
}

class _ItemDetailView extends ConsumerWidget {
  final ItemDetail item;
  const _ItemDetailView({required this.item});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return DefaultTabController(
      length: 3,
      child: Column(
        children: [
          _buildHeader(context, ref),
          const TabBar(
            tabs: [
              Tab(text: 'Fields'),
              Tab(text: 'eBay Draft'),
              Tab(text: 'Offers'),
            ],
          ),
          Expanded(
            child: TabBarView(
              children: [
                _buildFieldsTab(ref),
                _buildEbayTab(),
                _buildOffersTab(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeader(BuildContext context, WidgetRef ref) {
    final isOnline = ref.watch(connectionStatusProvider) == ConnectionStatus.online;
    final api = ref.read(apiClientProvider);
    final localPath = ref.read(offlineDbProvider).getLocalThumbnailPath(item.sku);

    Widget image;
    if (isOnline && item.images.isNotEmpty) {
      image = CachedNetworkImage(
        imageUrl: api.mediaUrl(item.images.first),
        fit: BoxFit.cover,
      );
    } else if (!isOnline && localPath != null && File(localPath).existsSync()) {
      image = Image.file(File(localPath), fit: BoxFit.cover);
    } else {
      image = Container(color: Colors.grey[200], child: const Icon(Icons.image_not_supported));
    }

    return Padding(
      padding: const EdgeInsets.all(16.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      item.sku,
                      style: const TextStyle(fontSize: 12, color: Colors.grey, fontFamily: 'monospace'),
                    ),
                    Text(
                      item.data['title'] ?? 'No Title',
                      style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              IconButton(
                icon: const Icon(Icons.edit, color: Colors.blue),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (context) => EditItemScreen(item: item)),
                ),
              ),
              const SizedBox(width: 8),
              SizedBox(
                width: 80,
                height: 80,
                child: image,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: [
              Chip(label: Text(item.data['location'] ?? 'Unknown')),
              Chip(label: Text(item.data['status'] ?? 'Unknown'), backgroundColor: Colors.blue[50]),
              Chip(label: Text('\$${item.data['price'] ?? '0.00'}'), backgroundColor: Colors.green[50]),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildFieldsTab(WidgetRef ref) {
    final fields = item.data;
    final isOnline = ref.watch(connectionStatusProvider) == ConnectionStatus.online;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _infoRow('Condition', fields['condition'] ?? 'Unknown'),
        _infoRow('Qty', fields['qty']?.toString() ?? '0'),
        _infoRow('Weight', fields['weight']?.toString() ?? '-'),
        _infoRow('Size Class', fields['size_class'] ?? '-'),
        _infoRow('Category Group', fields['category_group'] ?? '-'),
        _infoRow('eBay Category', fields['ebay_category_id']?.toString() ?? '-'),
        const Divider(),
        const Text('AI Identification Hint', style: TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(height: 4),
        Text(fields['ai_hint'] ?? 'None'),
        const SizedBox(height: 16),
        const Text('Photos', style: TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        if (isOnline)
          SizedBox(
            height: 120,
            child: ListView.builder(
              scrollDirection: Axis.horizontal,
              itemCount: item.images.length,
              itemBuilder: (context, index) {
                final api = ref.read(apiClientProvider);
                return Padding(
                  padding: const EdgeInsets.only(right: 8.0),
                  child: CachedNetworkImage(
                    imageUrl: api.mediaUrl(item.images[index]),
                    height: 120,
                    width: 120,
                    fit: BoxFit.cover,
                    errorWidget: (_, __, ___) => const Icon(Icons.broken_image),
                  ),
                );
              },
            ),
          )
        else
          const Text('Photos only available online', style: TextStyle(fontStyle: FontStyle.italic, color: Colors.grey)),
      ],
    );
  }

  Widget _buildEbayTab() {
    final draft = item.data['draft_listing'] ?? {};
    if (draft.isEmpty) {
      return const Center(child: Text('No eBay draft yet'));
    }
    final offerPrice = item.data['ebay_offer']?['price'];
    final draftPrice = draft['price'];
    final displayPrice = offerPrice ?? draftPrice;
    final priceStr = displayPrice != null
        ? '\$${(displayPrice as num).toStringAsFixed(2)}'
            '${offerPrice != null ? ' (offer)' : ''}'
        : '-';
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _infoRow('Draft Title', draft['title'] ?? '-'),
        _infoRow('Price', priceStr),
        const SizedBox(height: 8),
        const Text('Description', style: TextStyle(fontWeight: FontWeight.bold)),
        Text(draft['description'] ?? '-'),
        const Divider(),
        const Text('Aspects', style: TextStyle(fontWeight: FontWeight.bold)),
        ...?(draft['aspects'] as Map?)?.entries.map((e) => _infoRow(e.key, e.value.toString())),
      ],
    );
  }

  Widget _buildOffersTab() {
    final offer = item.data['ebay_offer'] ?? {};
    if (offer.isEmpty) {
      return const Center(child: Text('No active offers'));
    }
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _infoRow('Offer ID', offer['offer_id'] ?? '-'),
        _infoRow('Listing ID', offer['listing_id'] ?? '-'),
        _infoRow('Price', offer['price'] != null ? '\$${(offer['price'] as num).toStringAsFixed(2)}' : '-'),
        _infoRow('Available', offer['available_quantity']?.toString() ?? '-'),
        const Divider(),
        const ListTile(
          title: Text('View on eBay'),
          trailing: Icon(Icons.open_in_new),
          enabled: false, // TODO Phase D
        ),
      ],
    );
  }

  Widget _infoRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 120, child: Text(label, style: const TextStyle(color: Colors.grey))),
          Expanded(child: Text(value, style: const TextStyle(fontWeight: FontWeight.w500))),
        ],
      ),
    );
  }
}
