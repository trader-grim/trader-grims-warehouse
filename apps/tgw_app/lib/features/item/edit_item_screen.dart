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
        title: Text('Edit ${widget.item.sku}'),
        actions: [
          if (_isSaving)
            const Center(child: Padding(padding: EdgeInsets.all(16.0), child: CircularProgressIndicator()))
          else
            IconButton(icon: const Icon(Icons.save), onPressed: _save),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
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
            const Text('AI & Pipeline Actions', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            const SizedBox(height: 16),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ElevatedButton.icon(
                  onPressed: () => _performAction('ai_identify'),
                  icon: const Icon(Icons.psychology),
                  label: const Text('Re-identify'),
                ),
                ElevatedButton.icon(
                  onPressed: () => _performAction('ebay_draft'),
                  icon: const Icon(Icons.description),
                  label: const Text('Re-draft'),
                ),
                ElevatedButton.icon(
                  onPressed: () => _performAction('ebay_price'),
                  icon: const Icon(Icons.sell),
                  label: const Text('Re-price'),
                ),
                ElevatedButton.icon(
                  onPressed: () => _performAction('thumbnail_gen'),
                  icon: const Icon(Icons.image),
                  label: const Text('Regen Thumb'),
                ),
              ],
            ),
            const SizedBox(height: 32),
            const Text('Advanced Actions', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.orange)),
            const SizedBox(height: 8),
            Row(
              children: [
                ElevatedButton(
                  onPressed: () => _performAction('ebay_stage'),
                  style: ElevatedButton.styleFrom(backgroundColor: Colors.orange[100], foregroundColor: Colors.orange[900]),
                  child: const Text('Stage for eBay'),
                ),
                const SizedBox(width: 8),
                ElevatedButton(
                  onPressed: () => _performAction('ebay_publish'),
                  style: ElevatedButton.styleFrom(backgroundColor: Colors.green[100], foregroundColor: Colors.green[900]),
                  child: const Text('Publish to eBay'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
