"""Tests for tgw.items — write operations and list_items search filters."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tgw.api import list_items
from tgw.items import (
    catlocmvall,
    create_item,
    titleupdate,
    update_item,
    update_items,
    verifiedupdate,
)


def make_item(root: Path, sku: str, **fields) -> Path:
    item_dir = root / sku
    item_dir.mkdir(parents=True, exist_ok=True)
    doc = {'sku': sku, **fields}
    path = item_dir / f'{sku}.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


def make_cfg(root: Path) -> dict:
    return {
        'itemdata_root':       root,
        'location_tree_root':  root / 'by-location',
        'search_catalog_path': root / '_no-search-catalog.json',
        'full_catalog_path':   root / '_no-full-catalog.json',
        'skip_missing':        True,
        'pretty':              True,
    }


def read_item(root: Path, sku: str) -> dict:
    return json.loads((root / sku / f'{sku}.json').read_text())


# ---------------------------------------------------------------------------

def test_update_item_changes_field():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', title='Old Title')
        cfg = make_cfg(root)
        result = update_item(cfg, 'tgw20260101000000001', 'title', 'New Title')
        assert result['ok'] is True
        assert read_item(root, 'tgw20260101000000001')['title'] == 'New Title'


def test_create_item_creates_parent_dir():
    # Todo #1311: create_item() must mkdir the parent dir before writing —
    # previously only the http_server.py caller did this, so calling
    # create_item() directly on a SKU with no existing directory raised
    # FileNotFoundError from the underlying atomic write (untested prior
    # to this fix).
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        cfg = make_cfg(root)
        sku = 'tgw20260101000000099'
        assert not (root / sku).exists()
        path = create_item(cfg, sku, {'title': 'New Item'})
        assert path.exists()
        assert read_item(root, sku)['title'] == 'New Item'


def test_create_item_existing_sku_raises():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000098', title='Existing')
        cfg = make_cfg(root)
        import pytest
        with pytest.raises(FileExistsError):
            create_item(cfg, 'tgw20260101000000098', {'title': 'Dup'})


def test_update_item_missing_sku():
    with tempfile.TemporaryDirectory() as d:
        cfg = make_cfg(Path(d))
        result = update_item(cfg, 'tgw99999999999999999', 'title', 'x')
        assert result['ok'] is False


def test_write_field_rejects_negative_qty():
    import pytest

    from tgw.items import _write_field
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sku = "tgw001"
        d_sku = root / sku
        d_sku.mkdir()
        (d_sku / f"{sku}.json").write_text(json.dumps({"sku": sku, "qty": 1}), encoding="utf-8")
        cfg = {"itemdata_root": root}
        with pytest.raises(ValueError, match="cannot be negative"):
            _write_field(cfg, sku, "qty", -1)


def test_update_item_check_only():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', title='Old')
        cfg = make_cfg(root)
        result = update_item(cfg, 'tgw20260101000000001', 'title', 'New',
                             check_only=True)
        assert result['ok'] is True
        assert result['check_only'] is True
        assert read_item(root, 'tgw20260101000000001')['title'] == 'Old'


def test_update_items_bulk():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skus = {'tgw20260101000000001', 'tgw20260101000000002'}
        for sku in skus:
            make_item(root, sku, title='Old')
        cfg = make_cfg(root)
        result = update_items(cfg, skus, 'title', 'Bulk Title')
        assert result['ok'] is True
        assert result['count'] == 2
        for sku in skus:
            assert read_item(root, sku)['title'] == 'Bulk Title'


def test_titleupdate():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', title='Old')
        cfg = make_cfg(root)
        result = titleupdate(cfg, 'tgw20260101000000001', 'New Title')
        assert result['ok'] is True
        assert read_item(root, 'tgw20260101000000001')['title'] == 'New Title'


def test_verifiedupdate():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001')
        cfg = make_cfg(root)
        result = verifiedupdate(cfg, 'tgw20260101000000001', 'yes')
        assert result['ok'] is True
        item = read_item(root, 'tgw20260101000000001')
        assert item['verified'] == 'yes'
        assert item['#STATUS'] == 'In Stock'


def test_catlocmvall_moves_all():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for sku in ['tgw20260101000000001', 'tgw20260101000000002']:
            make_item(root, sku, location='OLD')
        make_item(root, 'tgw20260101000000003', location='OTHER')
        cfg = make_cfg(root)
        result = catlocmvall(cfg, 'OLD', 'NEW')
        assert result['ok'] is True
        assert result['count'] == 2
        assert read_item(root, 'tgw20260101000000001')['location'] == 'NEW'
        assert read_item(root, 'tgw20260101000000002')['location'] == 'NEW'
        assert read_item(root, 'tgw20260101000000003')['location'] == 'OTHER'


def test_catlocmvall_empty_location():
    with tempfile.TemporaryDirectory() as d:
        cfg = make_cfg(Path(d))
        result = catlocmvall(cfg, 'EMPTY', 'NEW')
        assert result['ok'] is True
        assert result['count'] == 0


# ---------------------------------------------------------------------------
# list_items --empty FIELD (tgw search --empty FIELD)
# ---------------------------------------------------------------------------

def test_list_items_empty_field_null_or_missing():
    """--empty returns items where the field is absent or null; non-empty excluded."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001')                 # location absent
        make_item(root, 'tgw20260101000000002', location=None)  # location null
        make_item(root, 'tgw20260101000000003', location='A1')  # has a location
        cfg = make_cfg(root)
        result = list_items(cfg, empty_field='location')
        skus = {i['sku'] for i in result['items']}
        assert 'tgw20260101000000001' in skus
        assert 'tgw20260101000000002' in skus
        assert 'tgw20260101000000003' not in skus


def test_list_items_empty_field_empty_string():
    """--empty matches empty-string and whitespace-only values; non-empty excluded."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', location='')    # empty string
        make_item(root, 'tgw20260101000000002', location='  ')  # whitespace
        make_item(root, 'tgw20260101000000003', location='B2')  # non-empty
        cfg = make_cfg(root)
        result = list_items(cfg, empty_field='location')
        skus = {i['sku'] for i in result['items']}
        assert 'tgw20260101000000001' in skus
        assert 'tgw20260101000000002' in skus
        assert 'tgw20260101000000003' not in skus
