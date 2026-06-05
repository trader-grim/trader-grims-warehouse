"""
tgw.scrub — ItemData maintenance and field normalization passes.

Each pass is idempotent and safe to re-run. Always dry-run first.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from .items import atomic_write_json
from .resolver import load_item_doc

log = logging.getLogger(__name__)


def data_scrub_pass1(cfg: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    """
    Pass 1: rename ``#VERIFIED`` → ``verified`` across all item JSONs.

    - Renames the field in-place, preserving all other fields and key order.
    - Items that already have ``verified`` (lowercase) are left as-is.
    - Items without ``#VERIFIED`` are counted as skipped (nothing to do).
    - Does NOT touch ``#STATUS`` — Python code actively reads that field;
      that rename requires coordinated Python source changes.

    Returns a summary dict. If ``dry_run=True`` no files are written.
    """
    itemdata_root = Path(cfg['itemdata_root'])

    renamed:  List[Dict[str, Any]] = []
    skipped:  int = 0
    errors:   List[Dict[str, Any]] = []

    for sku_dir in sorted(itemdata_root.iterdir()):
        if not sku_dir.is_dir():
            continue
        sku  = sku_dir.name
        path = sku_dir / f'{sku}.json'
        if not path.exists():
            continue

        try:
            doc = load_item_doc(path)
        except Exception as exc:
            errors.append({'sku': sku, 'error': str(exc)})
            continue

        if '#VERIFIED' not in doc:
            skipped += 1
            continue

        if 'verified' in doc:
            # Already has the target field — skip to avoid silent overwrite.
            skipped += 1
            log.debug('%s: already has "verified" key; skipping', sku)
            continue

        old_value = doc['#VERIFIED']
        new_doc   = {('verified' if k == '#VERIFIED' else k): v
                     for k, v in doc.items()}

        if not dry_run:
            atomic_write_json(path, new_doc)
            log.debug('%s: #VERIFIED → verified (%r)', sku, old_value)

        renamed.append({'sku': sku, 'value': old_value})

    result: Dict[str, Any] = {
        'ok':               True,
        'pass':             1,
        'description':      '#VERIFIED → verified rename',
        'dry_run':          dry_run,
        'renamed':          len(renamed),
        'skipped':          skipped,
        'errors':           len(errors),
    }
    if errors:
        result['error_detail'] = errors[:10]
    if dry_run:
        result['sample_would_rename'] = renamed[:5]
    return result
