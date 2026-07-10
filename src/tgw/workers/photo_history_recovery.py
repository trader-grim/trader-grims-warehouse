#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Iterable


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))




def normalize_name(name: str) -> str:
    return Path(name.strip()).name.lower().replace(' ', '_')


def extract_photo_refs(itemdata: dict, keys: list[str]) -> list[str]:
    refs: list[str] = []
    for key in keys:
        value = itemdata.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace(';', ',').split(',')]
            refs.extend([p for p in parts if p])
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, str) and v.strip():
                    refs.append(v.strip())
                elif isinstance(v, dict):
                    for candidate in ('file', 'filename', 'path', 'name'):
                        if candidate in v and isinstance(v[candidate], str) and v[candidate].strip():
                            refs.append(v[candidate].strip())
                            break
        elif isinstance(value, dict):
            for candidate in ('file', 'filename', 'path', 'name'):
                if candidate in value and isinstance(value[candidate], str) and value[candidate].strip():
                    refs.append(value[candidate].strip())
                    break
    out = []
    seen = set()
    for ref in refs:
        n = normalize_name(ref)
        if n not in seen:
            seen.add(n)
            out.append(ref)
    return out


def build_index(search_roots: list[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for root in search_roots:
        if not root.exists():
            continue
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                p = Path(dirpath) / fn
                index.setdefault(normalize_name(fn), []).append(p)
    return index


def rank_matches(paths: list[Path]) -> list[Path]:
    def score(p: Path) -> tuple[int, int, str]:
        s = str(p).replace('\\\\', '/').lower()
        bonus = 0
        if '/history/magento/magento_photos/product/' in s:
            bonus -= 1000
        elif '/history/' in s:
            bonus -= 100
        return (bonus, len(s), s)
    return sorted(paths, key=score)


def find_matches(ref: str, index: dict[str, list[Path]]) -> list[Path]:
    return rank_matches(index.get(normalize_name(ref), []))


def ensure_copy(src: Path, dst: Path, overwrite: bool = False, write: bool = False) -> str:
    # audit#1143 #1170: this script had no dry-run gate at all — every match
    # was copied straight into live ItemData with no review step, unlike the
    # tools/ near-duplicate this was copied from (which defaults to dry-run
    # and requires --write). Mirror that same convention here.
    if dst.exists() and not overwrite:
        return 'exists'
    if not write:
        return 'would_copy'
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return 'copied'


def process_item(itemdata_path: Path, cfg: dict, index: dict[str, list[Path]],
                 write: bool = False) -> list[dict]:
    raw = itemdata_path.read_text(encoding='utf-8').strip()
    if not raw:
        logging.warning('Skipping empty item file: %s', itemdata_path)
        return []
    try:
        itemdata = json.loads(raw)
    except json.JSONDecodeError as e:
        logging.warning('Skipping malformed item file %s: %s', itemdata_path, e)
        return []
    if not isinstance(itemdata, dict):
        logging.warning('Skipping non-dict item file %s: got %s', itemdata_path, type(itemdata).__name__)
        return []
    refs = extract_photo_refs(itemdata, cfg['photo_reference_keys'])
    item_folder = itemdata_path.parent
    results: list[dict] = []
    for ref in refs:
        matches = find_matches(ref, index)
        if not matches:
            results.append({'itemdata': str(itemdata_path), 'photo_ref': ref, 'action': 'missing', 'source_path': None, 'dest_path': None, 'all_matches': []})
            continue
        src = matches[0]
        dest = item_folder / src.name
        if dest.exists() and not cfg['destination'].get('overwrite', False):
            action = 'exists'
        else:
            action = ensure_copy(
                src, dest,
                overwrite=bool(cfg['destination'].get('overwrite', False)),
                write=write,
            ) if cfg['destination'].get('copy_if_missing', True) else 'skipped'
        results.append({'itemdata': str(itemdata_path), 'photo_ref': ref, 'action': action, 'source_path': str(src), 'dest_path': str(dest), 'all_matches': [str(p) for p in matches]})
    return results


def write_report(rows: list[dict], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def iter_itemdata_files(root: Path) -> Iterable[Path]:
    """Yield only canonical SKU JSON files: ItemData/SKU/SKU.json"""
    for child in sorted(root.iterdir()):
        if child.is_dir():
            candidate = child / f'{child.name}.json'
            if candidate.exists():
                yield candidate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--itemdata')
    ap.add_argument('--report')
    ap.add_argument('--write', action='store_true',
                    help='Actually copy files (default is dry-run)')
    args = ap.parse_args()

    if not args.config:
        ap.error('must supply --config')

    config_path = Path(args.config)

    cfg = load_config(config_path)
    mode = 'WRITE' if args.write else 'DRY-RUN'
    logging.info('photo_history_recovery: starting — mode=%s', mode)
    index = build_index([Path(p) for p in cfg['default_search_roots']])
    item_files = [Path(args.itemdata)] if args.itemdata else list(iter_itemdata_files(Path(cfg['itemdata_root'])))
    rows: list[dict] = []
    for item_file in item_files:
        rows.extend(process_item(item_file, cfg, index, write=args.write))
    report_path = Path(args.report) if args.report else Path('output/photo_recovery_report.jsonl')
    write_report(rows, report_path)
    would_copy = sum(1 for r in rows if r['action'] == 'would_copy')
    if not args.write and would_copy:
        logging.info('Dry-run: run with --write to copy %d photos.', would_copy)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
