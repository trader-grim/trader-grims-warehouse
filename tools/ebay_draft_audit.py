#!/usr/bin/env python3
"""
ebay_draft aspect-fill audit
"""

import json
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.resolver import find_item_jsons, load_item_doc

def run_audit() -> None:
    # Load configuration
    cfg = load_config(Path(DEFAULT_CONFIG))
    item_jsons = find_item_jsons(cfg)

    stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        'total_items': 0,
        'req_total': 0,
        'req_filled': 0,
        'rec_total': 0,
        'rec_filled': 0,
    })

    # Iterate over all item JSON files to aggregate data
    for json_path in item_jsons:
        try:
            item = load_item_doc(json_path)
            draft = item.get('draft_listing')
            if not draft:
                continue

            # Group statistics by category
            cat_id = str(draft.get('category_id', 'unknown'))
            cat_name = str(draft.get('category_name', 'unknown'))
            key = f"{cat_id} ({cat_name})"

            s = stats[key]
            s['total_items'] += 1
            s['req_total'] += draft.get('aspects_required_total', 0)
            s['req_filled'] += draft.get('aspects_required_filled', 0)
            s['rec_total'] += draft.get('aspects_recommended_total', 0)
            s['rec_filled'] += draft.get('aspects_recommended_filled', 0)

        except Exception as e:
            print(f"Error processing {json_path}: {e}")

    # Calculate fill rates and identify coverage gaps
    report = []
    for cat, s in stats.items():
        if s['total_items'] == 0: continue
        
        req_rate = (s['req_filled'] / s['req_total']) if s['req_total'] > 0 else 1.0
        rec_rate = (s['rec_filled'] / s['rec_total']) if s['rec_total'] > 0 else 1.0
        
        report.append({
            'category': cat,
            'items': s['total_items'],
            'req_fill_rate': req_rate,
            'rec_fill_rate': rec_rate,
        })

    # Sort by worst requirement coverage
    report.sort(key=lambda x: x['req_fill_rate'])

    # Generate prompt-tuning recommendations
    for r in report:
        if r['req_fill_rate'] < 0.8:
            r['recommendation'] = 'Review taxonomy mappings, check if aspect definition is too restrictive, or tune prompt to prioritize missing specifics.'
        elif r['rec_fill_rate'] < 0.5:
            r['recommendation'] = 'Consider adding more examples to Browse API hint context or prompt.'
        else:
            r['recommendation'] = 'Healthy.'

    # Save report to inbox/
    inbox_path = Path('docs/TGW-Plan-Vault/inbox/ebay_draft_audit.json')
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    with inbox_path.open('w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    print(f"Audit report saved to {inbox_path}")

if __name__ == '__main__':
    run_audit()
