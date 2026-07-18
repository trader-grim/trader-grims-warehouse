#!/usr/bin/env python3
"""
backfill_sku_history_1412.py — recover sku_history rows lost to the
2026-06-24 pg_restore during the NixOS/CatioNIX migration cutover.

Investigation (todo #1412, PP-ADD-005):
  - sku_history had only 3,305 rows, all class 'A' / changed_by
    'sku_migrate_script', dated 2026-06-24..2026-06-29 — the *live-eBay*
    batch phase run by the ebay_sku_migrate worker (queue-driven, ongoing,
    NOT missing data -- just not finished, currently ~3.3k of ~8.3k planned).
  - rename_sku() was NOT bypassed for the bulk (non-live) migration on
    2026-06-03/04 -- it always writes sku_history when dry_run=False (see
    src/tgw/sku_migration.py::_record_history, called from rename_sku()).
  - The bulk run's rows were LOST, not skipped: commit 234ff84
    ("post-NixOS migration -- restore ... schema") explicitly notes a
    pg_restore around 2026-06-24 that caused "sequence loss" on other
    tables too (todo_items, ai_usage) -- sku_history's June 3-4 rows did
    not survive that restore/schema-reinit, while the filesystem renames
    themselves (which don't depend on Postgres) obviously did.
  - Recovery source: the 4 rollback manifests in /opt/TGW/var/log/
    (sku-migrate-*.json, all dry_run:false), which record old_sku, new_sku,
    class for every rename actually executed that night. Cross-checked:
    26,652 unique (old, new) pairs across all 4 manifests, and ALL 26,652
    are confirmed still in effect on disk right now (new_sku dir exists,
    old_sku dir does not) -- zero mismatches. Zero overlap with the SKUs
    already in sku_history (the live-eBay batch is a disjoint set).

What this script does NOT claim:
  - The manifests do not carry a per-item timestamp, only a per-manifest
    `generated_at` (written just before that batch's renames executed).
    Backfilled rows use the *manifest's* generated_at as changed_at --
    this is a close approximation (minutes, not the exact per-item instant)
    and is explicitly marked as such via changed_by/notes, following the
    `updated_at_backfilled` precedent in inventory_record.py: a backfilled
    value is flagged, never presented as an original real-time capture.
  - had_ebay_listing is set False for all these rows -- correct by
    construction, since every source manifest has include_live_ebay:false.

Usage:
  sudo -u tgw python3 scripts/backfill_sku_history_1412.py            # dry-run (default)
  sudo -u tgw python3 scripts/backfill_sku_history_1412.py --apply    # real INSERT
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import psycopg2

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.logging import announce_script_run

MANIFESTS = [
    Path('/opt/TGW/var/log/sku-migrate-20260603T215536.json'),
    Path('/opt/TGW/var/log/sku-migrate-20260604T041914.json'),
    Path('/opt/TGW/var/log/sku-migrate-20260604T043012.json'),
    Path('/opt/TGW/var/log/sku-migrate-20260604T044841.json'),
]

DATA_ROOT = Path('/opt/TGW/data/ItemData')

CHANGED_BY = 'sku_migrate_backfill_1412'


def build_backfill_rows() -> list[dict]:
    """
    Merge all manifests into one old_sku -> row map. Where an old_sku
    appears in more than one manifest (the 9,434 items re-planned in the
    044841 follow-up run after the 043012 run left them un-renamed), keep
    the LATER manifest's generated_at -- it's the closer approximation to
    when that item's rename actually completed.
    """
    rows: dict[str, dict] = {}
    for path in MANIFESTS:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('dry_run') is not False:
            continue  # only real (already-executed) manifests
        generated_at = manifest['generated_at']
        for r in manifest['renames']:
            rows[r['old']] = {
                'sku_old': r['old'],
                'sku_new': r['new'],
                'changed_at': generated_at,
                'change_reason': f"normalize_class_{r['class'].lower()}",
                'changed_by': CHANGED_BY,
                'had_ebay_listing': False,
                'notes': (
                    f"backfilled from {path.name} (todo #1412); original "
                    "sku_history row lost in the 2026-06-24 pg_restore "
                    "during NixOS/CatioNIX migration cutover (commit "
                    "234ff84); changed_at is manifest generation time, "
                    "not the exact per-item completion instant"
                ),
                '_source_manifest': path.name,
            }
    return list(rows.values())


def verify_on_disk(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split rows into (confirmed, unconfirmed) based on current filesystem
    state -- only backfill renames we can positively verify actually took
    effect (new dir exists, old dir doesn't). Never fabricate a row for a
    rename we can't confirm happened."""
    confirmed, unconfirmed = [], []
    for row in rows:
        new_exists = (DATA_ROOT / row['sku_new']).is_dir()
        old_exists = (DATA_ROOT / row['sku_old']).is_dir()
        if new_exists and not old_exists:
            confirmed.append(row)
        else:
            unconfirmed.append(row)
    return confirmed, unconfirmed


def existing_sku_old(cfg) -> set[str]:
    with psycopg2.connect(cfg['postgres_dsn']) as con:
        with con.cursor() as cur:
            cur.execute("SELECT sku_old FROM sku_history")
            return {r[0] for r in cur.fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                     help='perform the real INSERT (default: dry-run report only)')
    args = ap.parse_args()

    announce_script_run(
        'backfill_sku_history_1412.py',
        'backfill sku_history rows lost to 2026-06-24 pg_restore, '
        'sourced from /opt/TGW/var/log/sku-migrate-*.json manifests',
        apply=args.apply,
    )

    cfg = load_config(Path(DEFAULT_CONFIG))

    rows = build_backfill_rows()
    print(f"manifest-derived candidate rows (deduped by old_sku): {len(rows)}")

    confirmed, unconfirmed = verify_on_disk(rows)
    print(f"confirmed still in effect on disk: {len(confirmed)}")
    print(f"NOT confirmed on disk (will be skipped, never fabricated): {len(unconfirmed)}")
    for r in unconfirmed[:20]:
        print(f"  skip: {r['sku_old']} -> {r['sku_new']} (source {r['_source_manifest']})")

    already_in_db = existing_sku_old(cfg)
    to_insert = [r for r in confirmed if r['sku_old'] not in already_in_db]
    already_present = len(confirmed) - len(to_insert)
    print(f"already present in sku_history (skipped, no duplicate insert): {already_present}")
    print(f"rows to insert: {len(to_insert)}")

    if not args.apply:
        print("\n-- DRY RUN, no changes made. Sample of what would be inserted: --")
        for r in to_insert[:10]:
            print(f"  {r['sku_old']} -> {r['sku_new']}  ({r['change_reason']}, "
                  f"changed_at={r['changed_at']})")
        print(f"\nRe-run with --apply to insert {len(to_insert)} rows.")
        return 0

    with psycopg2.connect(cfg['postgres_dsn']) as con:
        with con.cursor() as cur:
            cur.execute("SELECT count(*) FROM sku_history")
            before = cur.fetchone()[0]
            for r in to_insert:
                cur.execute(
                    """
                    INSERT INTO sku_history
                        (sku_old, sku_new, changed_at, change_reason,
                         changed_by, had_ebay_listing, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (r['sku_old'], r['sku_new'], r['changed_at'],
                     r['change_reason'], r['changed_by'],
                     r['had_ebay_listing'], r['notes']),
                )
            con.commit()
            cur.execute("SELECT count(*) FROM sku_history")
            after = cur.fetchone()[0]

    print(f"sku_history row count: {before} -> {after} (+{after - before})")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
