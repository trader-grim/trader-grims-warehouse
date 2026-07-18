#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""catpick_backfill_candidates.py — PP-CATPICK-001 Phase 1 (todo #1079).

Backfill `category_candidates` (name + full ancestor path) onto every group
in category-groups.json, sourced entirely from the on-disk eBay category
tree cache (`catalog_root/ebay-category-tree.json`) — zero live API calls,
per the packet's own constraint.

Each group's `ebay_categories` (a curated shortlist of 3-6 category IDs) gets
a matching `category_candidates` list:

    "category_candidates": [
        {"id": "261186", "name": "Books", "path": ["Books"]},
        ...
    ]

`path` is the full ancestor chain (root-level name first, this category's
name last) — the design's own rationale (project-smart-category-picker
memory): a bare leaf name like "Books" is ambiguous out of context; the full
branch disambiguates it without needing a separate hint field.

Default is dry-run (prints what would change); pass --apply to write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw import items  # noqa: E402
from tgw.apis.ebay.taxonomy import _ensure_tree_index  # noqa: E402
from tgw.config import DEFAULT_CONFIG, load_config  # noqa: E402
from tgw.logging import announce_script_run, setup_logging  # noqa: E402


def _ancestor_path(tree_index: Dict[str, Dict[str, Any]], category_id: str) -> List[str]:
    """Root-first list of category names from the top of the tree down to
    (and including) category_id. Missing/unknown IDs return just the ID
    itself as a single-element path (never silently drop a candidate)."""
    if category_id not in tree_index:
        return [category_id]
    chain: List[str] = []
    node_id: Any = category_id
    seen = set()
    while node_id and node_id not in seen:
        seen.add(node_id)
        node = tree_index.get(node_id)
        if not node:
            break
        chain.append(node['name'])
        node_id = node.get('parent_id')
    chain.reverse()
    return chain


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                       help='Write changes (default: dry-run/report only)')
    args = parser.parse_args()

    # No prior logging configuration in this script (verified live, todo
    # #1369) — without it, announce_script_run()'s event is silently
    # dropped (default root level WARNING, no handlers).
    try:
        setup_logging('tgw.catpick_backfill_candidates')
    except OSError:
        pass  # no writable log root (e.g. CI/test env) — announce still attempted below
    announce_script_run(
        'catpick_backfill_candidates.py',
        'backfill category_candidates onto every category-groups.json group from the on-disk eBay category tree cache',
        apply=args.apply,
    )

    cfg = load_config(Path(DEFAULT_CONFIG))
    groups_path: Path = cfg['category_groups_path']
    doc = json.loads(groups_path.read_text(encoding='utf-8'))
    groups = doc.get('groups', {})

    print('Loading eBay category tree cache (zero API calls if fresh) ...', flush=True)
    tree_index = _ensure_tree_index(cfg)
    print(f'{len(tree_index)} categories indexed.', flush=True)

    unknown_ids: List[str] = []
    for group_key, group in groups.items():
        candidates = []
        for cat_id in group.get('ebay_categories', []):
            path = _ancestor_path(tree_index, cat_id)
            name = tree_index.get(cat_id, {}).get('name', cat_id)
            if cat_id not in tree_index:
                unknown_ids.append(f'{group_key}: {cat_id}')
            candidates.append({'id': cat_id, 'name': name, 'path': path})
        group['category_candidates'] = candidates
        print(f'{group_key} ({group.get("name", "")}): '
             f'{len(candidates)} candidate(s)')
        for c in candidates:
            print(f'    {c["id"]:>8s}  {" > ".join(c["path"])}')

    if unknown_ids:
        print(f'\nWARNING: {len(unknown_ids)} category ID(s) not found in the tree '
             f'cache (kept as bare-ID fallback, not dropped):')
        for u in unknown_ids:
            print(f'  {u}')

    if not args.apply:
        print('\n[DRY-RUN] no changes written — pass --apply to write.')
        return 0

    items.atomic_write_json(groups_path, doc, pretty=True)
    print(f'\n[APPLIED] category_candidates backfilled for {len(groups)} groups '
         f'-> {groups_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
