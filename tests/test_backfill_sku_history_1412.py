"""todo #1412 (PP-ADD-005): sku_history rows for the 2026-06-03/04 bulk SKU
migration were lost to the 2026-06-24 pg_restore during the NixOS/CatioNIX
migration cutover. scripts/backfill_sku_history_1412.py recovers them from
the /opt/TGW/var/log/sku-migrate-*.json rollback manifests written by that
run.

All filesystem paths are monkeypatched to a tmp_path fixture -- no real
/opt/TGW/var/log or /opt/TGW/data/ItemData paths are touched, no DB
connection is made (DB-touching functions are exercised separately, not
called by these tests). Fully offline.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'backfill_sku_history_1412.py'
_spec = importlib.util.spec_from_file_location('backfill_sku_history_1412', _SCRIPT_PATH)
backfill_mod = importlib.util.module_from_spec(_spec)
sys.modules['backfill_sku_history_1412'] = backfill_mod
_spec.loader.exec_module(backfill_mod)


def _write_manifest(path: Path, generated_at: str, dry_run: bool, renames: list[dict]) -> None:
    path.write_text(json.dumps({
        'generated_at': generated_at,
        'dry_run': dry_run,
        'renames': renames,
    }), encoding='utf-8')


def test_build_backfill_rows_dedupes_keeping_later_manifest(tmp_path, monkeypatch):
    m1 = tmp_path / 'sku-migrate-1.json'
    m2 = tmp_path / 'sku-migrate-2.json'
    _write_manifest(m1, '2026-06-04T04:30:17+00:00', False, [
        {'old': 'tgwOLD1', 'new': 'tgwNEW1', 'class': 'A'},
        {'old': 'tgwOLD2', 'new': 'tgwNEW2', 'class': 'A'},
    ])
    # OLD1 re-planned in a later follow-up manifest -- later timestamp should win
    _write_manifest(m2, '2026-06-04T04:48:44+00:00', False, [
        {'old': 'tgwOLD1', 'new': 'tgwNEW1', 'class': 'A'},
    ])
    monkeypatch.setattr(backfill_mod, 'MANIFESTS', [m1, m2])

    rows = backfill_mod.build_backfill_rows()
    by_old = {r['sku_old']: r for r in rows}

    assert set(by_old) == {'tgwOLD1', 'tgwOLD2'}
    assert by_old['tgwOLD1']['changed_at'] == '2026-06-04T04:48:44+00:00'
    assert by_old['tgwOLD2']['changed_at'] == '2026-06-04T04:30:17+00:00'
    assert by_old['tgwOLD1']['had_ebay_listing'] is False
    assert by_old['tgwOLD1']['changed_by'] == 'sku_migrate_backfill_1412'
    assert by_old['tgwOLD1']['change_reason'] == 'normalize_class_a'
    assert 'backfilled' in by_old['tgwOLD1']['notes']


def test_build_backfill_rows_skips_dry_run_manifests(tmp_path, monkeypatch):
    m1 = tmp_path / 'sku-migrate-dry.json'
    _write_manifest(m1, '2026-06-04T04:30:17+00:00', True, [
        {'old': 'tgwOLD1', 'new': 'tgwNEW1', 'class': 'A'},
    ])
    monkeypatch.setattr(backfill_mod, 'MANIFESTS', [m1])

    rows = backfill_mod.build_backfill_rows()
    assert rows == []


def test_verify_on_disk_only_confirms_completed_renames(tmp_path, monkeypatch):
    data_root = tmp_path / 'ItemData'
    (data_root / 'tgwNEW1').mkdir(parents=True)  # rename completed
    (data_root / 'tgwOLD2').mkdir(parents=True)  # rename never happened (old still there)
    monkeypatch.setattr(backfill_mod, 'DATA_ROOT', data_root)

    rows = [
        {'sku_old': 'tgwOLD1', 'sku_new': 'tgwNEW1'},
        {'sku_old': 'tgwOLD2', 'sku_new': 'tgwNEW2'},
    ]
    confirmed, unconfirmed = backfill_mod.verify_on_disk(rows)

    assert [r['sku_old'] for r in confirmed] == ['tgwOLD1']
    assert [r['sku_old'] for r in unconfirmed] == ['tgwOLD2']


def test_verify_on_disk_rejects_if_old_dir_still_present(tmp_path, monkeypatch):
    # A rename can't be trusted as complete if BOTH old and new dirs exist
    # (e.g. a partial/interrupted operation) -- never fabricate a row for it.
    data_root = tmp_path / 'ItemData'
    (data_root / 'tgwNEW1').mkdir(parents=True)
    (data_root / 'tgwOLD1').mkdir(parents=True)
    monkeypatch.setattr(backfill_mod, 'DATA_ROOT', data_root)

    rows = [{'sku_old': 'tgwOLD1', 'sku_new': 'tgwNEW1'}]
    confirmed, unconfirmed = backfill_mod.verify_on_disk(rows)

    assert confirmed == []
    assert [r['sku_old'] for r in unconfirmed] == ['tgwOLD1']
