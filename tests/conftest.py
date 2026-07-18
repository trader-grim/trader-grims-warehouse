"""Shared test helpers and fixtures for TGW test suite."""

import json
from pathlib import Path


def make_fake_fence_write(itemdata_root):
    """Return a fake fence_ebay_write that merges eBay blocks into the item JSON on disk.

    Simulates what the fence server does in production, so tests that read
    the item JSON back after worker.handle() see the expected fields.
    """
    def fake_fence_ebay_write(cfg, sku, ebay_offer=None, ebay_listing=None,
                              ebay_submitted=None, ebay_live=None,
                              allow_protected=None):
        root = Path(cfg.get('itemdata_root', itemdata_root))
        p = root / sku / f'{sku}.json'
        doc = json.loads(p.read_text(encoding='utf-8'))
        for key, val in [('ebay_offer', ebay_offer), ('ebay_listing', ebay_listing),
                         ('ebay_submitted', ebay_submitted), ('ebay_live', ebay_live)]:
            if val is not None:
                doc[key] = {**doc.get(key, {}), **val}
        p.write_text(json.dumps(doc), encoding='utf-8')
        return {'ok': True}
    return fake_fence_ebay_write


def make_fake_patch_item(itemdata_root):
    """Return a fake fence_patch_item that merges top-level fields into item JSON."""
    def fake_fence_patch_item(cfg, sku, fields):
        root = Path(cfg.get('itemdata_root', itemdata_root))
        p = root / sku / f'{sku}.json'
        doc = json.loads(p.read_text(encoding='utf-8'))
        doc.update(fields)
        p.write_text(json.dumps(doc), encoding='utf-8')
        return {'ok': True}
    return fake_fence_patch_item


def make_fake_create_item(itemdata_root):
    """Return a fake fence_create_item that writes the item JSON directly to
    disk under itemdata_root, mirroring what the real fence server does —
    for testing worker code that calls tgw.apis.fence.create_item without
    a live http_server.
    """
    def fake_fence_create_item(cfg, sku, data):
        root = Path(cfg.get('itemdata_root', itemdata_root))
        d = root / sku
        d.mkdir(parents=True, exist_ok=True)
        p = d / f'{sku}.json'
        record = {'sku': sku, **data}
        p.write_text(json.dumps(record), encoding='utf-8')
        return {'ok': True, 'sku': sku, 'path': str(p)}
    return fake_fence_create_item


def make_fake_fence_write_tmp(tmp_path):
    """Like make_fake_fence_write but resolves sku path via tmp_path directly."""
    import json
    from pathlib import Path

    def fake_fence_ebay_write(cfg, sku, ebay_offer=None, ebay_listing=None,
                              ebay_submitted=None, ebay_live=None,
                              allow_protected=None):
        # Try cfg['itemdata_root'] first, then fall back to tmp_path
        root = cfg.get('itemdata_root') or tmp_path
        p = Path(root) / sku / f'{sku}.json'
        if not p.exists():
            # Fallback: try tmp_path directly
            p = Path(tmp_path) / sku / f'{sku}.json'
        if p.exists():
            doc = json.loads(p.read_text(encoding='utf-8'))
            for key, val in [('ebay_offer', ebay_offer), ('ebay_listing', ebay_listing),
                             ('ebay_submitted', ebay_submitted), ('ebay_live', ebay_live)]:
                if val is not None:
                    doc[key] = {**doc.get(key, {}), **val}
            p.write_text(json.dumps(doc), encoding='utf-8')
        return {'ok': True}
    return fake_fence_ebay_write


def make_fake_patch_item_tmp(tmp_path):
    """Like make_fake_patch_item but resolves sku path via tmp_path directly."""
    import json
    from pathlib import Path

    def fake_fence_patch_item(cfg, sku, fields):
        root = cfg.get('itemdata_root') or tmp_path
        p = Path(root) / sku / f'{sku}.json'
        if not p.exists():
            p = Path(tmp_path) / sku / f'{sku}.json'
        if p.exists():
            doc = json.loads(p.read_text(encoding='utf-8'))
            doc.update(fields)
            p.write_text(json.dumps(doc), encoding='utf-8')
        return {'ok': True}
    return fake_fence_patch_item
