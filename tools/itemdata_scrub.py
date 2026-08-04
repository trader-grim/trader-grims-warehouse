#!/usr/bin/env python3
"""
itemdata_scrub.py — ItemData key scrubber and history builder.

Pass 1: Merge title/description/location variant keys into history lists.
Pass 2: Remove keys not in preserve_keys and matching remove_patterns/remove_keys.

Idempotent — safe to run multiple times.
Dry-run by default. Pass --write to commit changes.

Usage:
    python tools/itemdata_scrub.py --config /opt/TGW/config/tgw-api-config.json \
                                   --rules  /opt/TGW/config/queue-workers/itemdata_scrub_denylist.json \
                                   [--write] [--sku SKU] [--limit N] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from tgw.logging import announce_script_run

log = logging.getLogger('itemdata_scrub')

# ---------------------------------------------------------------------------
# Keys that get merged into history lists rather than just deleted
# ---------------------------------------------------------------------------

TITLE_KEYS = [
    'title', 'Title', 'Item Title', 'Listing title', 'item_title',
    'title0', 'title1', 'title2', 'title3', 'title4',
    '202112_Title', 'tgw_name', 'tgw_product_name',
    'name', 'Name', 'product_name', 'm2_name',
]

DESCRIPTION_KEYS = [
    'description', 'Description', 'description1', 'description22',
    'ms_description', 'condition_description', 'gpt_desc',
    'm2_additional_attributes',
]

LOCATION_KEYS = [
    'location', '#LOCATION', 'tgw_location',
]

# The canonical keys we write history into
TITLE_HISTORY_KEY       = 'title_history'
DESCRIPTION_HISTORY_KEY = 'description_history'
LOCATION_HISTORY_KEY    = 'location_history'

# The canonical current-value keys we always preserve
CANONICAL_TITLE       = 'title'
CANONICAL_DESCRIPTION = 'description'
CANONICAL_LOCATION    = 'location'


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    with path.open('r', encoding='utf-8', errors='replace') as f:
        return json.load(f)


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', delete=False, dir=path.parent
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2, sort_keys=False)
        tmp.write('\n')
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# History building
# ---------------------------------------------------------------------------

def _collect_unique(existing: Any, candidates: List[Any]) -> List[Any]:
    """Return a deduplicated list starting from existing history."""
    seen: Set[str] = set()
    result: List[Any] = []
    for item in (existing if isinstance(existing, list) else []):
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)
    for item in candidates:
        if item is None:
            continue
        v = str(item).strip()
        if not v:
            continue
        key = json.dumps(v, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(v)
    return result


def build_histories(doc: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Merge title/description/location variant keys into history lists.

    Returns (updated_doc, list_of_changes).
    The canonical current-value key is preserved as-is.
    Variant keys are merged into history and then removed from the doc.
    """
    changes: List[str] = []
    doc = dict(doc)

    for canonical, history_key, variant_keys in [
        (CANONICAL_TITLE,       TITLE_HISTORY_KEY,       TITLE_KEYS),
        (CANONICAL_DESCRIPTION, DESCRIPTION_HISTORY_KEY, DESCRIPTION_KEYS),
        (CANONICAL_LOCATION,    LOCATION_HISTORY_KEY,    LOCATION_KEYS),
    ]:
        candidates = []
        keys_to_remove = []
        for k in variant_keys:
            if k == canonical:
                continue  # keep canonical key, just collect its value
            if k in doc:
                v = doc[k]
                if v is not None and str(v).strip():
                    candidates.append(str(v).strip())
                keys_to_remove.append(k)

        if candidates:
            existing = doc.get(history_key, [])
            merged = _collect_unique(existing, candidates)
            if merged != doc.get(history_key):
                doc[history_key] = merged
                changes.append(
                    f'merged {len(candidates)} value(s) into {history_key}'
                )

        for k in keys_to_remove:
            if k in doc:
                del doc[k]
                changes.append(f'removed variant key {k!r} -> {history_key}')

    return doc, changes


# ---------------------------------------------------------------------------
# Key scrubbing
# ---------------------------------------------------------------------------

def compile_patterns(patterns: List[str]) -> List[re.Pattern]:
    return [re.compile(p) for p in patterns]


def should_remove(key: str, preserve: Set[str],
                  remove_exact: Set[str],
                  remove_patterns: List[re.Pattern]) -> bool:
    """Return True if this key should be removed."""
    # History keys are always preserved
    if key in (TITLE_HISTORY_KEY, DESCRIPTION_HISTORY_KEY, LOCATION_HISTORY_KEY):
        return False
    if key in preserve:
        return False
    if key in remove_exact:
        return True
    for pat in remove_patterns:
        if pat.search(key):
            return True
    return False


def scrub_keys(doc: Dict[str, Any],
               preserve: Set[str],
               remove_exact: Set[str],
               remove_patterns: List[re.Pattern]) -> Tuple[Dict[str, Any], List[str]]:
    """Remove keys that don't belong. Returns (cleaned_doc, removed_keys)."""
    removed: List[str] = []
    cleaned: Dict[str, Any] = {}
    for k, v in doc.items():
        if should_remove(k, preserve, remove_exact, remove_patterns):
            removed.append(k)
        else:
            cleaned[k] = v
    return cleaned, removed


# ---------------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------------

def process_item(json_path: Path,
                 preserve: Set[str],
                 remove_exact: Set[str],
                 remove_patterns: List[re.Pattern],
                 write: bool,
                 verbose: bool) -> Dict[str, Any]:
    """Process one item. Returns a summary dict."""
    sku = json_path.parent.name
    result = {'sku': sku, 'path': str(json_path),
              'history_changes': [], 'removed_keys': [],
              'written': False, 'skipped': False, 'error': None}
    try:
        raw = json_path.read_text(encoding='utf-8', errors='replace').strip()
        if not raw:
            result['skipped'] = True
            result['error'] = 'empty file'
            return result

        doc = json.loads(raw)
        if not isinstance(doc, dict):
            result['skipped'] = True
            result['error'] = f'not a dict: {type(doc).__name__}'
            return result

        # Pass 1: history
        doc, history_changes = build_histories(doc)
        result['history_changes'] = history_changes

        # Pass 2: scrub
        doc, removed = scrub_keys(doc, preserve, remove_exact, remove_patterns)
        result['removed_keys'] = removed

        changed = bool(history_changes or removed)
        if changed and write:
            atomic_write_json(json_path, doc)
            result['written'] = True

        if verbose and changed:
            log.info('SKU %s: %d history changes, %d keys removed%s',
                     sku, len(history_changes), len(removed),
                     ' [written]' if result['written'] else ' [dry-run]')

    except Exception as e:
        result['error'] = str(e)
        log.error('SKU %s: %s', sku, e)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def iter_item_jsons(itemdata_root: Path):
    """Yield canonical SKU JSON paths."""
    if not itemdata_root.exists():
        return
    for child in sorted(itemdata_root.iterdir()):
        if child.is_dir():
            candidate = child / f'{child.name}.json'
            if candidate.exists():
                yield candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description='TGW ItemData key scrubber and history builder'
    )
    parser.add_argument('--config', default='/opt/TGW/config/tgw-api-config.json',
                        help='tgw-api config (for itemdata_root)')
    parser.add_argument('--rules',
                        default='/opt/TGW/config/queue-workers/itemdata_scrub_denylist.json',
                        help='Scrub rules JSON')
    parser.add_argument('--write', action='store_true',
                        help='Commit changes (default is dry-run)')
    parser.add_argument('--sku', default=None,
                        help='Process only this SKU')
    parser.add_argument('--limit', type=int, default=None,
                        help='Stop after N items')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    announce_script_run(
        'itemdata_scrub.py',
        'merge/remove denylisted ItemData keys per scrub rules (tools/ standalone variant)',
        write=args.write, config=args.config, rules=args.rules,
        sku=args.sku, limit=args.limit,
    )

    # Load configs
    api_cfg = load_json(Path(args.config))
    rules   = load_json(Path(args.rules))

    itemdata_root = Path(api_cfg.get('itemdata_root', '/opt/TGW/data/ItemData'))
    preserve      = set(rules.get('preserve_keys', []))
    remove_exact  = set(rules.get('remove_keys', []))
    remove_pats   = compile_patterns(rules.get('remove_patterns', []))

    # Always preserve canonical keys and history keys
    preserve |= {
        CANONICAL_TITLE, CANONICAL_DESCRIPTION, CANONICAL_LOCATION,
        TITLE_HISTORY_KEY, DESCRIPTION_HISTORY_KEY, LOCATION_HISTORY_KEY,
        'sku',
    }

    mode = 'WRITE' if args.write else 'DRY-RUN'
    log.info('Starting itemdata scrub — mode=%s root=%s', mode, itemdata_root)
    started = time.time()

    # Collect paths
    if args.sku:
        paths = [itemdata_root / args.sku / f'{args.sku}.json']
    else:
        paths = list(iter_item_jsons(itemdata_root))

    if args.limit:
        paths = paths[:args.limit]

    total = len(paths)
    log.info('Items to process: %d', total)

    results = [
        process_item(p, preserve, remove_exact, remove_pats,
                     args.write, args.verbose)
        for p in paths
    ]

    # Summary
    written  = sum(1 for r in results if r['written'])
    changed  = sum(1 for r in results if r['history_changes'] or r['removed_keys'])
    skipped  = sum(1 for r in results if r['skipped'])
    errors   = sum(1 for r in results if r['error'])
    all_removed: Dict[str, int] = {}
    for r in results:
        for k in r['removed_keys']:
            all_removed[k] = all_removed.get(k, 0) + 1

    elapsed = round(time.time() - started, 1)
    log.info('Done in %.1fs — %d items, %d changed, %d written, %d skipped, %d errors',
             elapsed, total, changed, written, skipped, errors)

    if all_removed:
        top = sorted(all_removed.items(), key=lambda x: -x[1])[:20]
        log.info('Top removed keys:')
        for k, n in top:
            log.info('  %5d  %s', n, k)

    if not args.write and changed:
        log.info('Dry-run complete. Run with --write to commit %d changes.', changed)

    return 0 if errors == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
