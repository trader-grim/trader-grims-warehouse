"""
tgw.assets — Photo asset ordering utilities.

Single source of truth for photo ordering. Workers and HTTP endpoints
call ordered_photos() / primary_photo() instead of doing their own
iterdir()/sorted() logic. This ensures the user's photo_order is
respected everywhere: catalog, eBay upload, AI vision, thumbnail gen.

Photo order precedence:
  1. item['image'] basename — explicit operator-designated primary
  2. item['photo_order'] list — explicit user ordering
  3. Natural sort (name-based, numeric segments sorted numerically)

Extras (files on disk not in photo_order) are merged in natural sort
position but never before index 0 (the user's chosen primary).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

PHOTO_EXTS: frozenset[str] = frozenset({
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.tif', '.tiff',
})


def _nat_key(name: str) -> tuple:
    # Sort '-alt' variants after their base file; keep everything else by natural order.
    is_alt = '-alt.' in name
    base = name.replace('-alt.', '.') if is_alt else name
    parts = tuple(int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', base))
    return (parts, int(is_alt))


def _photos_nat(sku_dir: Path) -> List[str]:
    """All photo filenames in sku_dir, natural-sorted."""
    return sorted(
        (p.name for p in sku_dir.iterdir()
         if p.is_file() and p.suffix.lower() in PHOTO_EXTS),
        key=_nat_key,
    )


def ordered_photos(item: Dict[str, Any], sku_dir: Path) -> List[Path]:
    """Return photo Paths in display order.

    Applies photo_order from item JSON. Extras (files on disk not yet
    in photo_order) are merged in natural sort position, but never
    before index 0 (the user's chosen primary).
    """
    all_names = _photos_nat(sku_dir)
    photo_order: List[str] = item.get('photo_order') or []

    if not photo_order:
        return [sku_dir / n for n in all_names]

    img_set = set(all_names)
    ordered = [n for n in photo_order if n in img_set]
    ordered_set = set(ordered)
    extras = [n for n in all_names if n not in ordered_set]

    if not extras:
        return [sku_dir / n for n in ordered]

    nat_rank = {n: i for i, n in enumerate(all_names)}
    result = list(ordered)
    for extra in extras:
        ep = nat_rank[extra]
        insert_at = len(result)
        for j in range(1, len(result)):   # never displace primary at index 0
            if nat_rank.get(result[j], 0) > ep:
                insert_at = j
                break
        result.insert(insert_at, extra)
    return [sku_dir / n for n in result]


def primary_photo(item: Dict[str, Any], sku_dir: Path) -> Optional[Path]:
    """Return the primary photo path.

    Checks item['image'] (operator-designated primary) first, falling back
    to ordered_photos() (photo_order then natural sort).
    """
    image_field = item.get('image')
    if image_field:
        candidate = sku_dir / Path(image_field).name
        if candidate.is_file():
            return candidate
    photos = ordered_photos(item, sku_dir)
    return photos[0] if photos else None
