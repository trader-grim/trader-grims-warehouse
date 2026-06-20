"""
tgw.thumbnail — Thumbnail cache builder.

Generates per-SKU JPEG thumbnails from the primary item photo.
Primary image: basename of the 'image' field in the item JSON,
resolved against the SKU directory. Falls back to the first .jpg
alphabetically if the named file is missing.

Requires Pillow (pip install Pillow). Returns ok=False gracefully if missing.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from PIL import Image
    _PILLOW = True
except ImportError:
    _PILLOW = False

from .assets import primary_photo as _asset_primary_photo
from .resolver import find_item_jsons, load_item_doc


def build_thumbnail_for_sku(cfg: Dict[str, Any], sku: str) -> Dict[str, Any]:
    """Generate the thumbnail for a single SKU. Returns {ok, sku, action}."""
    if not _PILLOW:
        return {'ok': False, 'sku': sku, 'error': 'Pillow not installed'}
    sku_dir: Path = cfg['itemdata_root'] / sku
    json_path = sku_dir / f'{sku}.json'
    if not json_path.exists():
        return {'ok': False, 'sku': sku, 'error': 'item JSON not found'}
    doc = load_item_doc(json_path)
    img_path = _asset_primary_photo(doc, sku_dir)
    if img_path is None:
        return {'ok': True, 'sku': sku, 'action': 'skipped', 'reason': 'no image'}
    thumb_root: Path = cfg['thumbnail_root']
    thumb_path = thumb_root / f'{sku}.jpg'
    if (thumb_path.exists() and
            thumb_path.stat().st_mtime >= img_path.stat().st_mtime):
        return {'ok': True, 'sku': sku, 'action': 'skipped', 'reason': 'up to date'}
    thumb_root.mkdir(parents=True, exist_ok=True)
    size: Tuple[int, int] = tuple(cfg['thumbnail_size'])
    with Image.open(img_path) as img:
        img.thumbnail(size)
        img.convert('RGB').save(thumb_path, 'JPEG', quality=85)
    return {'ok': True, 'sku': sku, 'action': 'generated', 'thumb': str(thumb_path)}



def build_thumbnail_cache(cfg: Dict[str, Any],
                          check_only: bool = False) -> Dict[str, Any]:
    """Generate thumbnails for all items in ItemData."""
    if not _PILLOW:
        return {'ok': False, 'artifact': 'thumbnail_cache',
                'error': 'Pillow not installed — pip install Pillow'}

    started = time.time()
    thumb_root: Path = cfg['thumbnail_root']
    size: Tuple[int, int] = tuple(cfg['thumbnail_size'])
    item_jsons = find_item_jsons(cfg)

    generated, skipped = 0, 0
    errors: List[str] = []

    for json_path in item_jsons:
        try:
            doc = load_item_doc(json_path)
            sku = str(doc.get('sku', '')).strip()
            if not sku:
                skipped += 1
                continue
            img_path = _asset_primary_photo(doc, json_path.parent)
            if img_path is None:
                skipped += 1
                continue
            thumb_path = thumb_root / f'{sku}.jpg'
            if not check_only:
                if (thumb_path.exists() and
                        thumb_path.stat().st_mtime >= img_path.stat().st_mtime):
                    skipped += 1
                    continue
                thumb_root.mkdir(parents=True, exist_ok=True)
                with Image.open(img_path) as img:
                    img.thumbnail(size)
                    img.convert('RGB').save(thumb_path, 'JPEG', quality=85)
            generated += 1
        except Exception as e:
            errors.append(f'{json_path.parent.name}: {e}')

    elapsed = round(time.time() - started, 3)
    result: Dict[str, Any] = {
        'ok': len(errors) == 0,
        'artifact': 'thumbnail_cache',
        'path': str(thumb_root),
        'generated': generated,
        'skipped': skipped,
        'elapsed_seconds': elapsed,
        'check_only': check_only,
    }
    if errors:
        result['errors'] = errors
    return result
