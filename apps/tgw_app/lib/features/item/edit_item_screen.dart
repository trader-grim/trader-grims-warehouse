import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/providers.dart';
import '../../models/models.dart';

class EditItemScreen extends ConsumerStatefulWidget {
  final ItemDetail item;
  const EditItemScreen({super.key, required this.item});

  @override
  ConsumerState<EditItemScreen> createState() => _EditItemScreenState();
}

class _EditItemScreenState extends ConsumerState<EditItemScreen> {
  late TextEditingController _titleController;
  late TextEditingController _priceController;
  late TextEditingController _hintController;
  String? _selectedCondition;
  bool _isSaving = false;
  List<Map<String, dynamic>> _aspects = [];
  final Map<String, TextEditingController> _aspectControllers = {};
  bool _isLoadingAspects = false;

  final List<String> _conditions = ["", "New", "Like New", "Very Good", "Good", "Acceptable"];

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(text: widget.item.data['title'] ?? '');
    _priceController = TextEditingController(text: widget.item.data['price']?.toString() ?? '');
    _hintController = TextEditingController(text: widget.item.data['ai_hint'] ?? '');
    _selectedCondition = widget.item.data['condition'];
    if (!_conditions.contains(_selectedCondition)) {
      _selectedCondition = "";
    }
    
    _initializeAspects();
    _fetchAspects();
  }

  void _initializeAspects() {
    final existingAspects = widget.item.data['draft_listing']?['aspects'] as Map? ?? {};
    existingAspects.forEach((key, value) {
      _aspectControllers[key] = TextEditingController(text: value.toString());
    });
  }

  Future<void> _fetchAspects() async {
    final categoryId = widget.item.data['ebay_category_id']?.toString();
    if (categoryId == null || categoryId.isEmpty) return;

    setState(() => _isLoadingAspects = true);
    try {
      final aspects = await ref.read(repositoryProvider).getEbayAspects(categoryId);
      if (mounted) {
        setState(() {
          _aspects = aspects;
          for (final aspect in aspects) {
            final name = aspect['localizedAspectName'];
            if (!_aspectControllers.containsKey(name)) {
              _aspectControllers[name] = TextEditingController();
            }
          }
          _isLoadingAspects = false;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _isLoadingAspects = false);
    }
  }

  @override
  void dispose() {
    _titleController.dispose();
    _priceController.dispose();
    _hintController.dispose();
    _aspectControllers.forEach((_, c) => c.dispose());
    super.dispose();
  }

  Future<void> _save() async {
    setState(() => _isSaving = true);
    
    final Map<String, String> aspects = {};
    _aspectControllers.forEach((key, controller) {
      if (controller.text.isNotEmpty) {
        aspects[key] = controller.text;
      }
    });

    final fields = {
      'title': _titleController.text,
      'price': _priceController.text,
      'ai_hint': _hintController.text,
      'condition': _selectedCondition,
      'draft_listing': {
        ...widget.item.data['draft_listing'] ?? {},
        'aspects': aspects,
      },
    };

    final success = await ref.read(repositoryProvider).patchItem(widget.item.sku, fields);
    
    if (mounted) {
      setState(() => _isSaving = false);
      if (success) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Item updated')));
        ref.invalidate(itemDetailProvider(widget.item.sku));
        Navigator.pop(context);
      } else {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Update failed')));
      }
    }
  }

  Future<void> _performAction(String action) async {
    final jobId = await ref.read(repositoryProvider).performAction(widget.item.sku, action);
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(jobId != null ? 'Action queued: $action (Job #$jobId)' : 'Action failed')),
      );
    }
  }

  Future<void> _confirmAndPerformAction(String action, String label, String description) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(label),
        content: Text(description),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Confirm')),
        ],
      ),
    );
    if (confirmed == true) await _performAction(action);
  }

  Widget _buildAspectField(Map<String, dynamic> aspect) {
    final name = aspect['localizedAspectName'];
    final controller = _aspectControllers[name]!;
    final constraint = aspect['aspectConstraint'] ?? {};
    final isRequired = constraint['aspectRequired'] == true;
    final mode = constraint['aspectMode'] ?? 'FREE_TEXT';
    final values = aspect['aspectValues'] as List? ?? [];

    return Padding(
      padding: const EdgeInsets.only(bottom: 16.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$name${isRequired ? " *" : ""}',
            style: TextStyle(fontWeight: isRequired ? FontWeight.bold : FontWeight.normal, fontSize: 12),
          ),
          const SizedBox(height: 4),
          if (mode == 'SELECTION_ONLY')
            DropdownButtonFormField<String>(
              initialValue: values.any((v) => v['localizedValue'] == controller.text) ? controller.text : null,
              items: [
                const DropdownMenuItem(value: null, child: Text('Select...')),
                ...values.map((v) => DropdownMenuItem(
                      value: v['localizedValue'] as String,
                      child: Text(v['localizedValue'] as String, overflow: TextOverflow.ellipsis),
                    )),
              ],
              onChanged: (val) => setState(() => controller.text = val ?? ''),
              decoration: const InputDecoration(border: OutlineInputBorder(), contentPadding: EdgeInsets.symmetric(horizontal: 10)),
            )
          else
            Column(
              children: [
                TextField(
                  controller: controller,
                  decoration: InputDecoration(
                    border: const OutlineInputBorder(),
                    contentPadding: const EdgeInsets.symmetric(horizontal: 10),
                    suffixIcon: controller.text.isNotEmpty
                        ? IconButton(icon: const Icon(Icons.clear), onPressed: () => setState(() => controller.text = ''))
                        : null,
                  ),
                ),
                if (values.isNotEmpty)
                  SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: values.map((v) {
                        final val = v['localizedValue'] as String;
                        return Padding(
                          padding: const EdgeInsets.only(right: 4.0),
                          child: ActionChip(
                            label: Text(val, style: const TextStyle(fontSize: 10)),
                            onPressed: () => setState(() => controller.text = val),
                          ),
                        );
                      }).toList(),
                    ),
                  ),
              ],
            ),
        ],
      ),
    );
  }

  Future<void> _uploadToInbox() async {
    final result = await FilePicker.pickFiles(
      dialogTitle: 'Upload to inbox',
      allowMultiple: false,
    );
    if (result == null || result.files.isEmpty) return;
    final path = result.files.single.path;
    if (path == null) return;

    final filename = await ref.read(repositoryProvider).uploadToInbox(File(path));
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(filename != null ? 'Uploaded to inbox: $filename' : 'Upload failed'),
        backgroundColor: filename != null ? Colors.green[700] : Colors.red[700],
      ));
    }
  }

  Future<void> _showTitleHistory() async {
    final search = _titleController.text;
    if (search.isEmpty) return;

    setState(() => _isSaving = true);
    try {
      final items = await ref.read(repositoryProvider).getItems(search: search, limit: 10);
      if (mounted) {
        setState(() => _isSaving = false);
        final titles = items.map((e) => e.title).toSet().toList();
        if (titles.isEmpty) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('No matching titles found')));
          return;
        }

        showDialog(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Title History'),
            content: SizedBox(
              width: double.maxFinite,
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: titles.length,
                itemBuilder: (context, index) => ListTile(
                  title: Text(titles[index], style: const TextStyle(fontSize: 14)),
                  onTap: () {
                    setState(() => _titleController.text = titles[index]);
                    Navigator.pop(context);
                  },
                ),
              ),
            ),
            actions: [
              TextButton.icon(
                icon: const Icon(Icons.upload_file, size: 16),
                label: const Text('Upload to inbox'),
                onPressed: () {
                  Navigator.pop(context);
                  _uploadToInbox();
                },
              ),
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Close'),
              ),
            ],
          ),
        );
      }
    } catch (e) {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Review: ${widget.item.sku}'),
        actions: [
          if (_isSaving)
            const Center(child: Padding(padding: EdgeInsets.all(16.0), child: CircularProgressIndicator()))
          else
            IconButton(icon: const Icon(Icons.save), tooltip: 'Save changes', onPressed: _save),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Card(
              color: Theme.of(context).colorScheme.primaryContainer,
              margin: const EdgeInsets.only(bottom: 20),
              child: Padding(
                padding: const EdgeInsets.all(12.0),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.info_outline, color: Theme.of(context).colorScheme.onPrimaryContainer),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        'Review and confirm this item before sending through the pipeline. '
                        'Edit fields as needed, save, then use the Pipeline Trigger section below to queue a stage.',
                        style: TextStyle(color: Theme.of(context).colorScheme.onPrimaryContainer),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            TextField(
              controller: _titleController,
              decoration: InputDecoration(
                labelText: 'Title',
                border: const OutlineInputBorder(),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.history),
                  onPressed: _showTitleHistory,
                  tooltip: 'Suggest from History',
                ),
              ),
              maxLines: 2,
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String>(
                    decoration: const InputDecoration(labelText: 'Condition', border: OutlineInputBorder()),
                    initialValue: _selectedCondition,
                    items: _conditions.map((c) => DropdownMenuItem(value: c, child: Text(c.isEmpty ? 'None' : c))).toList(),
                    onChanged: (val) => setState(() => _selectedCondition = val),
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: TextField(
                    controller: _priceController,
                    decoration: const InputDecoration(labelText: 'Price', border: OutlineInputBorder(), prefixText: '\$'),
                    keyboardType: TextInputType.number,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _hintController,
              decoration: const InputDecoration(labelText: 'AI Hint', border: OutlineInputBorder()),
              maxLines: 3,
            ),
            const SizedBox(height: 32),
            const Text('Item Specifics (Aspects)', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            const SizedBox(height: 16),
            if (_isLoadingAspects)
              const Center(child: CircularProgressIndicator())
            else if (_aspects.isEmpty)
              const Text('No aspects found for this category.', style: TextStyle(fontStyle: FontStyle.italic))
            else
              ..._aspects.map((aspect) => _buildAspectField(aspect)),
            const SizedBox(height: 32),
            const Divider(),
            const SizedBox(height: 8),
            const Text('Pipeline Trigger', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            const Text(
              'Save any field changes first, then queue a pipeline stage below.',
              style: TextStyle(fontSize: 13, color: Colors.grey),
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ElevatedButton.icon(
                  onPressed: () => _confirmAndPerformAction(
                    'ai_identify',
                    'Re-identify',
                    'Queue ai_identify for this item. The AI will re-analyse photos and overwrite the current identification.',
                  ),
                  icon: const Icon(Icons.psychology),
                  label: const Text('Re-identify'),
                ),
                ElevatedButton.icon(
                  onPressed: () => _confirmAndPerformAction(
                    'ebay_draft',
                    'Re-draft',
                    'Queue ebay_draft to regenerate the eBay listing draft from the current item data.',
                  ),
                  icon: const Icon(Icons.description),
                  label: const Text('Re-draft'),
                ),
                ElevatedButton.icon(
                  onPressed: () => _confirmAndPerformAction(
                    'ebay_price',
                    'Re-price',
                    'Queue ebay_price to recalculate the suggested price using current comps.',
                  ),
                  icon: const Icon(Icons.sell),
                  label: const Text('Re-price'),
                ),
                ElevatedButton.icon(
                  onPressed: () => _confirmAndPerformAction(
                    'thumbnail_gen',
                    'Regen Thumbnail',
                    'Queue thumbnail_gen to rebuild the thumbnail from the current primary photo.',
                  ),
                  icon: const Icon(Icons.image),
                  label: const Text('Regen Thumb'),
                ),
              ],
            ),
            const SizedBox(height: 24),
            const Text('Listing Actions', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.orange)),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ElevatedButton.icon(
                  onPressed: () => _confirmAndPerformAction(
                    'ebay_stage',
                    'Stage for eBay',
                    'Queue ebay_stage to create or update the eBay offer draft. The item will be staged but not yet published.',
                  ),
                  icon: const Icon(Icons.upload_outlined),
                  style: ElevatedButton.styleFrom(backgroundColor: Colors.orange[100], foregroundColor: Colors.orange[900]),
                  label: const Text('Stage for eBay'),
                ),
                ElevatedButton.icon(
                  onPressed: () => _confirmAndPerformAction(
                    'ebay_publish',
                    'Publish to eBay',
                    'Queue ebay_publish to push this item live on eBay immediately, bypassing the ready pool.',
                  ),
                  icon: const Icon(Icons.public),
                  style: ElevatedButton.styleFrom(backgroundColor: Colors.green[100], foregroundColor: Colors.green[900]),
                  label: const Text('Publish to eBay'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
