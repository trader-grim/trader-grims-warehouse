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
            atomic_write_json(path, new_doc, archive_root=cfg.get('archive_root'))
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


def data_scrub_qty_repair(cfg: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    """
    Pass 3: set qty=1 for any item where qty < 0.

    Returns a summary dict. If dry_run=True no files are written.
    """
    itemdata_root = Path(cfg['itemdata_root'])

    repaired: List[Dict[str, Any]] = []
    skipped: int = 0
    errors: List[Dict[str, Any]] = []

    for sku_dir in sorted(itemdata_root.iterdir()):
        if not sku_dir.is_dir():
            continue
        sku = sku_dir.name
        path = sku_dir / f'{sku}.json'
        if not path.exists():
            continue

        try:
            doc = load_item_doc(path)
        except Exception as exc:
            errors.append({'sku': sku, 'error': str(exc)})
            continue

        qty = doc.get('qty')
        if qty is None:
            skipped += 1
            continue

        try:
            qty_val = float(qty)
        except (TypeError, ValueError):
            skipped += 1
            continue

        if qty_val >= 0:
            skipped += 1
            continue

        if not dry_run:
            doc['qty'] = 1
            atomic_write_json(path, doc, archive_root=cfg.get('archive_root'))
            log.debug('%s: qty %r → 1', sku, qty)

        repaired.append({'sku': sku, 'old_qty': qty})

    result: Dict[str, Any] = {
        'ok':          True,
        'pass':        3,
        'description': 'qty < 0 → 1 repair',
        'dry_run':     dry_run,
        'repaired':    len(repaired),
        'skipped':     skipped,
        'errors':      len(errors),
    }
    if errors:
        result['error_detail'] = errors[:10]
    if dry_run:
        result['sample_would_repair'] = repaired[:5]
    return result


def data_scrub_size_class_backfill(cfg: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    """
    Pass 2: populate ``size_class`` (and ``category_group``) from category-groups.json defaults.

    For each item in ItemData:
    - If ``size_class`` is already set → skip.
    - If ``category_group`` is set → look up size_class from groups data → write size_class.
    - Else if ``ebay_category_id`` is set → reverse-map to group → write both fields.
    - Else → skip (no inference basis).

    Enqueues a catalog_rebuild job after writing unless dry_run or nothing changed.
    """
    from .ebay.pricing import _load_groups

    groups_data = _load_groups(cfg)
    groups = groups_data.get('groups', {})

    group_size: Dict[str, str] = {k: grp.get('size_class', '') for k, grp in groups.items()}
    cat_to_group: Dict[str, str] = {}
    for gk, grp in groups.items():
        for cat_id in grp.get('ebay_categories', []):
            cat_to_group[str(cat_id)] = gk

    itemdata_root = Path(cfg['itemdata_root'])
    updated: List[Dict[str, Any]] = []
    skipped: int = 0
    errors: List[Dict[str, Any]] = []

    for sku_dir in sorted(itemdata_root.iterdir()):
        if not sku_dir.is_dir():
            continue
        sku = sku_dir.name
        path = sku_dir / f'{sku}.json'
        if not path.exists():
            continue

        try:
            doc = load_item_doc(path)
        except Exception as exc:
            errors.append({'sku': sku, 'error': str(exc)})
            continue

        if doc.get('size_class'):
            skipped += 1
            continue

        fields: Dict[str, Any] = {}
        cat_group = doc.get('category_group')
        if cat_group and group_size.get(cat_group):
            fields['size_class'] = group_size[cat_group]
        else:
            cat = str(doc.get('ebay_category_id', ''))
            gk = cat_to_group.get(cat) if cat else None
            if gk and group_size.get(gk):
                fields['category_group'] = gk
                fields['size_class'] = group_size[gk]

        if not fields:
            skipped += 1
            continue

        if not dry_run:
            doc.update(fields)
            atomic_write_json(path, doc, archive_root=cfg.get('archive_root'))
            log.debug('%s: set %s', sku, fields)

        updated.append({'sku': sku, 'fields': fields})

    if not dry_run and updated:
        try:
            from .queue import state_machine as _sm
            _sm.init(cfg['postgres_dsn'])
            _sm.enqueue_catalog_rebuild('size_class_backfill')
        except Exception:
            pass

    result: Dict[str, Any] = {
        'ok':          True,
        'pass':        2,
        'description': 'size_class backfill from category_group / ebay_category_id',
        'dry_run':     dry_run,
        'updated':     len(updated),
        'skipped':     skipped,
        'errors':      len(errors),
    }
    if errors:
        result['error_detail'] = errors[:10]
    if dry_run:
        result['sample_would_update'] = updated[:10]
    return result
