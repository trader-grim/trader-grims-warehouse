#!/usr/bin/env python3
"""
Scrub `description_history` contamination from item JSON files.

GEMINI-004 finding: text from "John F. Rider Perpetual Troubleshooter's Manuals"
leaked into description_history of unrelated items via picklist bleed-through.
Pattern: \n\n\ntgw-pl::=::<location>:=:<text>  :=: appended to entries, often
followed by a duplicate copy of the standard boilerplate.

Usage:
    python3 tools/scrub_description_history.py --dry-run   # report only
    python3 tools/scrub_description_history.py              # apply fixes
    python3 tools/scrub_description_history.py --sku tgw201411151759014 ...
"""

import argparse
import json
import re
import tempfile
from pathlib import Path

from tgw.logging import announce_script_run, setup_logging

ITEM_DATA_ROOT = Path('/opt/TGW/data/ItemData')
# Contamination: John F. Rider manuals picklist line leaked from SHELF40
# into unrelated items' description_history (GEMINI-004 finding).
# Match the specific bleed string: SHELF40 location + "John F. Rider Perpetual"
# to avoid touching legitimate SHELF40 entries for other items.
CONTAMINATION_RE = re.compile(
    r'\n\n\ntgw-pl::=::SHELF40:=:John F.*',
    re.DOTALL,
)


def strip_contamination(entry: str) -> str:
    """Remove John F. Rider picklist contamination and everything following it."""
    return CONTAMINATION_RE.sub('', entry).strip()


def atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, dir=path.parent) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def scrub_item(path: Path, dry_run: bool) -> dict | None:
    """Return change report dict if contamination found, else None."""
    with path.open('r', encoding='utf-8') as f:
        data = json.load(f)

    # Skip items that ARE the John F. Rider manuals — their SHELF40 picklist line is legitimate
    title = data.get('title', '')
    if 'John F' in title and 'Rider' in title:
        return None

    dh = data.get('description_history')
    if not isinstance(dh, list):
        return None

    changed_entries = []
    new_dh = []
    for i, entry in enumerate(dh):
        # Fast-path: only process entries with the specific contamination marker
        if not isinstance(entry, str) or 'tgw-pl::=::SHELF40:=:John F' not in entry:
            new_dh.append(entry)
            continue
        cleaned = strip_contamination(entry)
        if cleaned == entry:
            new_dh.append(entry)
            continue
        changed_entries.append({
            'index': i,
            'original_len': len(entry),
            'cleaned_len': len(cleaned),
            'was_empty': cleaned == '',
        })
        if cleaned:
            new_dh.append(cleaned)
        # drop the entry entirely if stripping leaves nothing

    if not changed_entries:
        return None

    report = {
        'sku': data.get('sku', path.stem),
        'path': str(path),
        'entries_changed': len(changed_entries),
        'entries_dropped': sum(1 for e in changed_entries if e['was_empty']),
        'detail': changed_entries,
    }

    if not dry_run:
        data['description_history'] = new_dh
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='report without writing')
    parser.add_argument('--sku', nargs='+', metavar='SKU', help='limit to specific SKUs')
    args = parser.parse_args()

    # No prior logging configuration in this script (verified live, todo
    # #1369) — without it, announce_script_run()'s event is silently
    # dropped (default root level WARNING, no handlers).
    try:
        setup_logging('tgw.scrub_description_history')
    except OSError:
        pass  # no writable log root (e.g. CI/test env) — announce still attempted below
    announce_script_run(
        'scrub_description_history.py',
        'scrub description_history contamination (GEMINI-004 picklist bleed-through) from item JSON files',
        dry_run=args.dry_run, sku=args.sku,
    )

    if args.sku:
        paths = [ITEM_DATA_ROOT / sku / f'{sku}.json' for sku in args.sku]
        paths = [p for p in paths if p.exists()]
    else:
        paths = sorted(
            p for sku_dir in ITEM_DATA_ROOT.iterdir()
            if sku_dir.is_dir()
            for p in [sku_dir / f'{sku_dir.name}.json']
            if p.exists()
        )

    total = len(paths)
    reports = []

    for i, path in enumerate(paths, 1):
        if i % 5000 == 0:
            print(f'  scanned {i}/{total}...')
        try:
            report = scrub_item(path, dry_run=args.dry_run)
            if report:
                reports.append(report)
        except Exception as exc:
            print(f'  ERROR {path}: {exc}')

    mode = 'DRY RUN' if args.dry_run else 'APPLIED'
    print(f'\n[{mode}] {len(reports)} item(s) affected out of {total} scanned\n')
    for r in reports:
        dropped = f' ({r["entries_dropped"]} dropped)' if r['entries_dropped'] else ''
        print(f'  {r["sku"]}: {r["entries_changed"]} entr{"y" if r["entries_changed"]==1 else "ies"} cleaned{dropped}')

    if reports and args.dry_run:
        print('\nRe-run without --dry-run to apply.')


if __name__ == '__main__':
    main()
