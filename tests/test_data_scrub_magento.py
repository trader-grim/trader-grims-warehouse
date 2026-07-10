"""audit#1143 / todo #1235 (merged #1162+#1164) — data_scrub_magento.py's
--execute mode wrote item JSON with a raw json.dump() straight to the
target path, bypassing the fence + atomic_write_json entirely. It now
routes through items.strip_fields(), which gives it the atomic write and
archive-before-overwrite (invariant E5) every other item mutation gets.
"""

import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'data_scrub_magento.py'
_spec = importlib.util.spec_from_file_location('data_scrub_magento', _SCRIPT_PATH)
data_scrub_magento = importlib.util.module_from_spec(_spec)
sys.modules['data_scrub_magento'] = data_scrub_magento
_spec.loader.exec_module(data_scrub_magento)


def _make_item(root: Path, sku: str, **fields) -> Path:
    item_dir = root / sku
    item_dir.mkdir(parents=True, exist_ok=True)
    doc = {'sku': sku, **fields}
    path = item_dir / f'{sku}.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


def _cfg(root: Path) -> dict:
    return {'itemdata_root': root, 'archive_root': root.parent / 'archive', 'pretty': True}


def test_dry_run_reports_without_writing():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / 'ItemData'
        _make_item(root, 'tgw20260101000000001', MagentoID='m1', title='Widget')
        cfg = _cfg(root)

        n = data_scrub_magento.process_item(cfg, 'tgw20260101000000001', execute=False)

        assert n == 1
        doc = json.loads((root / 'tgw20260101000000001' / 'tgw20260101000000001.json').read_text())
        assert doc['MagentoID'] == 'm1'  # untouched in dry-run


def test_execute_removes_fields_and_archives_before_overwrite():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / 'ItemData'
        _make_item(root, 'tgw20260101000000002', MagentoID='m2', eBayItemID='e2', title='Gadget')
        cfg = _cfg(root)

        n = data_scrub_magento.process_item(cfg, 'tgw20260101000000002', execute=True)

        assert n == 2
        doc = json.loads((root / 'tgw20260101000000002' / 'tgw20260101000000002.json').read_text())
        assert 'MagentoID' not in doc
        assert 'eBayItemID' not in doc
        assert doc['title'] == 'Gadget'

        zpath = cfg['archive_root'] / 'tgw20260101000000002.zip'
        assert zpath.exists()
        with zipfile.ZipFile(zpath) as zf:
            assert len(zf.namelist()) == 1


def test_execute_is_noop_when_no_target_fields_present():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / 'ItemData'
        _make_item(root, 'tgw20260101000000003', title='Clean item')
        cfg = _cfg(root)

        n = data_scrub_magento.process_item(cfg, 'tgw20260101000000003', execute=True)

        assert n == 0
        assert not (cfg['archive_root'] / 'tgw20260101000000003.zip').exists()


def test_corrupt_json_does_not_raise_and_is_reported(tmp_path, capsys):
    """audit#1143 #1235 follow-up: switching to items.strip_fields() dropped
    the old try/except around JSON parse errors — one corrupt item used to
    kill the whole batch run. process_item() must isolate the error per-item,
    same as the original json.JSONDecodeError/OSError handling."""
    root = tmp_path / 'ItemData'
    sku = 'tgw20260101000000004'
    item_dir = root / sku
    item_dir.mkdir(parents=True)
    (item_dir / f'{sku}.json').write_text('{not valid json', encoding='utf-8')
    cfg = _cfg(root)

    n = data_scrub_magento.process_item(cfg, sku, execute=True)

    assert n == -1
    assert 'WARNING' in capsys.readouterr().err


def test_main_uses_cfg_itemdata_root_not_hardcoded_constant():
    """audit#1143 #1235 follow-up: the module used to enumerate SKUs from a
    hardcoded ITEM_DATA_ROOT constant separate from cfg['itemdata_root'] —
    removed entirely so the two can no longer silently drift apart."""
    assert not hasattr(data_scrub_magento, 'ITEM_DATA_ROOT')


def test_execute_clears_catalog_verified_as_documented_side_effect():
    """audit#1143 #1244 follow-up (code review): switching to
    items.strip_fields() silently inherited a side effect the old raw
    json.dump() implementation never had — clearing 'catalog_verified' on
    any item actually modified. Documented in both docstrings now; this
    test locks the behavior in so it can't silently change again."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / 'ItemData'
        _make_item(root, 'tgw20260101000000005', MagentoID='m5',
                  title='Thing', catalog_verified='2026-01-01')
        cfg = _cfg(root)

        data_scrub_magento.process_item(cfg, 'tgw20260101000000005', execute=True)

        doc = json.loads((root / 'tgw20260101000000005' / 'tgw20260101000000005.json').read_text())
        assert 'catalog_verified' not in doc
