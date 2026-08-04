#!/usr/bin/env python3
"""
photo_history_recovery.py — One-shot photo recovery for ItemData.

Finds SKUs that have no photos in their ItemData directory, then searches
history roots for matching photos and copies them in.

Does NOT modify any item JSON. Does NOT overwrite existing photos.
Read-only on item data. Write-only to ItemData photo directories.

Usage:
    python tools/photo_history_recovery.py \
        --config /opt/TGW/config/tgw-api-config.json \
        --rules  /opt/TGW/config/queue-workers/photo_history_recovery.config.json \
        [--write] [--sku SKU] [--limit N] [--report output/recovery_report.jsonl] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from tgw.logging import announce_script_run

log = logging.getLogger('photo_recovery')

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.tiff', '.bmp'}


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    with path.open('r', encoding='utf-8', errors='replace') as f:
        return json.load(f)


def load_item_doc(path: Path) -> Optional[Dict[str, Any]]:
    """Load item JSON safely. Returns None on any error."""
    try:
        raw = path.read_text(encoding='utf-8', errors='replace').strip()
        if not raw:
            return None
        doc = json.loads(raw)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Photo detection
# ---------------------------------------------------------------------------

def item_has_photos(item_dir: Path) -> bool:
    """True if any image files exist directly in the item directory."""
    for p in item_dir.iterdir():
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            return True
    return False


def extract_photo_refs(doc: Dict[str, Any],
                       ref_keys: List[str]) -> List[str]:
    """
    Extract all photo filename/path references from item JSON.
    Returns a flat deduplicated list of basenames.
    """
    refs: List[str] = []
    seen: Set[str] = set()

    def add(v: Any) -> None:
        if not v:
            return
        if isinstance(v, list):
            for item in v:
                add(item)
        elif isinstance(v, str):
            v = v.strip()
            if v:
                bn = Path(v).name
                if bn and bn not in seen:
                    seen.add(bn)
                    refs.append(bn)

    for key in ref_keys:
        add(doc.get(key))

    return refs


# ---------------------------------------------------------------------------
# Photo index
# ---------------------------------------------------------------------------

def build_photo_index(search_roots: List[Path]) -> Dict[str, List[Path]]:
    """
    Walk all search roots and build a filename → [path, ...] index.
    Case-insensitive key for matching.
    """
    index: Dict[str, List[Path]] = {}
    for root in search_roots:
        if not root.exists():
            log.warning('Search root does not exist: %s', root)
            continue
        log.info('Indexing %s ...', root)
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if Path(fn).suffix.lower() in IMAGE_SUFFIXES:
                    key = fn.lower()
                    full = Path(dirpath) / fn
                    index.setdefault(key, []).append(full)
    total = sum(len(v) for v in index.values())
    log.info('Index built: %d unique filenames, %d total files', len(index), total)
    return index


def find_matches(ref: str,
                 index: Dict[str, List[Path]]) -> List[Path]:
    """Find all indexed paths matching a photo reference basename."""
    return index.get(ref.lower(), [])


def rank_matches(paths: List[Path]) -> List[Path]:
    """Prefer higher-resolution looking paths, then alphabetical."""
    def score(p: Path) -> Tuple[int, str]:
        # prefer paths not containing 'thumb' or 'small'
        penalty = 1 if re.search(r'thumb|small|preview', str(p).lower()) else 0
        return (penalty, str(p).lower())
    return sorted(paths, key=score)


# ---------------------------------------------------------------------------
# Per-item recovery
# ---------------------------------------------------------------------------

def recover_item(item_dir: Path,
                 doc: Dict[str, Any],
                 ref_keys: List[str],
                 index: Dict[str, List[Path]],
                 overwrite: bool,
                 write: bool,
                 verbose: bool) -> List[Dict[str, Any]]:
    """
    Attempt to recover photos for one item.
    Returns a list of action records for the report.
    """
    sku = item_dir.name
    refs = extract_photo_refs(doc, ref_keys)
    rows: List[Dict[str, Any]] = []

    if not refs:
        return rows

    for ref in refs:
        dest = item_dir / ref
        if dest.exists() and not overwrite:
            rows.append({'sku': sku, 'ref': ref, 'action': 'exists',
                         'source': None, 'dest': str(dest)})
            continue

        matches = find_matches(ref, index)
        if not matches:
            rows.append({'sku': sku, 'ref': ref, 'action': 'not_found',
                         'source': None, 'dest': None})
            if verbose:
                log.debug('SKU %s: no match for %s', sku, ref)
            continue

        ranked = rank_matches(matches)
        src = ranked[0]

        if write:
            tmp_dest = dest.with_name(dest.name + f'.tmp{os.getpid()}')
            try:
                shutil.copy2(src, tmp_dest)
                os.replace(tmp_dest, dest)
                action = 'copied'
            except Exception as e:
                try:
                    tmp_dest.unlink(missing_ok=True)
                except Exception:
                    pass
                rows.append({'sku': sku, 'ref': ref, 'action': 'error',
                             'source': str(src), 'dest': str(dest),
                             'error': str(e)})
                log.error('SKU %s: copy failed %s -> %s: %s', sku, src, dest, e)
                continue
        else:
            action = 'would_copy'

        rows.append({'sku': sku, 'ref': ref, 'action': action,
                     'source': str(src), 'dest': str(dest),
                     'all_matches': [str(p) for p in ranked]})
        if verbose:
            log.debug('SKU %s: %s %s -> %s', sku, action, src.name, dest)

    return rows


# ---------------------------------------------------------------------------
# Item iteration
# ---------------------------------------------------------------------------

def iter_item_dirs(itemdata_root: Path) -> Iterator[Path]:
    """Yield canonical item directories."""
    if not itemdata_root.exists():
        return
    for child in sorted(itemdata_root.iterdir()):
        if child.is_dir() and (child / f'{child.name}.json').exists():
            yield child


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(rows: List[Dict[str, Any]], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    log.info('Report written: %s (%d rows)', report_path, len(rows))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description='One-shot photo recovery for TGW ItemData'
    )
    parser.add_argument('--config', default='/opt/TGW/config/tgw-api-config.json')
    parser.add_argument('--rules',
                        default='/opt/TGW/config/queue-workers/photo_history_recovery.config.json')
    parser.add_argument('--write', action='store_true',
                        help='Actually copy files (default is dry-run)')
    parser.add_argument('--all-items', action='store_true',
                        help='Process all items, not just those missing photos')
    parser.add_argument('--sku', default=None,
                        help='Process only this SKU')
    parser.add_argument('--limit', type=int, default=None,
                        help='Stop after N items')
    parser.add_argument('--report', default='output/photo_recovery_report.jsonl',
                        help='Report output path')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    announce_script_run(
        'photo_history_recovery.py',
        'recover missing item photos from history archives into ItemData (tools/ standalone variant)',
        write=args.write, config=args.config, all_items=args.all_items,
        sku=args.sku, limit=args.limit,
    )

    api_cfg = load_json(Path(args.config))
    rules   = load_json(Path(args.rules))

    itemdata_root = Path(api_cfg.get('itemdata_root', '/opt/TGW/data/ItemData'))
    search_roots  = [Path(p) for p in rules.get('default_search_roots', [])]
    ref_keys      = rules.get('photo_reference_keys', [])
    overwrite     = rules.get('destination', {}).get('overwrite', False)

    mode = 'WRITE' if args.write else 'DRY-RUN'
    log.info('Starting photo recovery — mode=%s', mode)
    started = time.time()

    # Build the photo index once — bulk operation
    index = build_photo_index(search_roots)

    # Collect item dirs
    if args.sku:
        item_dirs = [itemdata_root / args.sku]
    else:
        item_dirs = list(iter_item_dirs(itemdata_root))

    if args.limit:
        item_dirs = item_dirs[:args.limit]

    log.info('Items to check: %d', len(item_dirs))

    all_rows: List[Dict[str, Any]] = []
    processed = skipped = no_refs = already_have = 0

    for item_dir in item_dirs:
        # Skip items that already have photos unless --all-items
        if not args.all_items and item_has_photos(item_dir):
            already_have += 1
            continue

        doc = load_item_doc(item_dir / f'{item_dir.name}.json')
        if doc is None:
            skipped += 1
            continue

        rows = recover_item(item_dir, doc, ref_keys, index,
                            overwrite, args.write, args.verbose)
        if not rows:
            no_refs += 1
        else:
            all_rows.extend(rows)
            processed += 1

    # Summary
    elapsed = round(time.time() - started, 1)
    copied       = sum(1 for r in all_rows if r['action'] == 'copied')
    would_copy   = sum(1 for r in all_rows if r['action'] == 'would_copy')
    not_found    = sum(1 for r in all_rows if r['action'] == 'not_found')
    errors       = sum(1 for r in all_rows if r['action'] == 'error')

    log.info('Done in %.1fs', elapsed)
    log.info('  Items checked:       %d', processed)
    log.info('  Already have photos: %d', already_have)
    log.info('  No photo refs:       %d', no_refs)
    log.info('  Skipped (bad JSON):  %d', skipped)
    log.info('  Photos copied:       %d', copied)
    log.info('  Would copy (dry-run):%d', would_copy)
    log.info('  Not found:           %d', not_found)
    log.info('  Errors:              %d', errors)

    if all_rows:
        write_report(all_rows, Path(args.report))

    if not args.write and (would_copy > 0):
        log.info('Dry-run: run with --write to copy %d photos.', would_copy)

    return 0 if errors == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
