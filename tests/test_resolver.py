"""Tests for tgw.resolver — no filesystem required for most cases."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

# remove: import pytest   (F401)
from tgw.resolver import iter_all_skus, resolve, sku_date_str

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_item(root: Path, sku: str, **fields) -> Path:
    """Create a minimal item JSON in a temp ItemData root."""
    item_dir = root / sku
    item_dir.mkdir(parents=True, exist_ok=True)
    doc = {'sku': sku, **fields}
    path = item_dir / f'{sku}.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


def make_cfg(itemdata_root: Path, location_tree_root: Path | None = None) -> dict:
    return {
        'itemdata_root':      itemdata_root,
        'location_tree_root': location_tree_root or (itemdata_root.parent / 'by-location'),
        'skip_missing':       True,
    }


# ---------------------------------------------------------------------------
# sku_date_str
# ---------------------------------------------------------------------------

def test_sku_date_str_valid():
    assert sku_date_str('tgw20260529143000000') == '20260529'


def test_sku_date_str_invalid():
    assert sku_date_str('not-a-sku') is None


# ---------------------------------------------------------------------------
# iter_all_skus
# ---------------------------------------------------------------------------

def test_iter_all_skus_empty():
    with tempfile.TemporaryDirectory() as d:
        cfg = make_cfg(Path(d))
        assert list(iter_all_skus(cfg)) == []


def test_iter_all_skus_finds_items():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001')
        make_item(root, 'tgw20260101000000002')
        cfg = make_cfg(root)
        skus = list(iter_all_skus(cfg))
        assert 'tgw20260101000000001' in skus
        assert 'tgw20260101000000002' in skus


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

def test_resolve_no_selectors_returns_all():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', location='A1')
        make_item(root, 'tgw20260101000000002', location='A2')
        cfg = make_cfg(root)
        result = resolve(cfg)
        assert result == {'tgw20260101000000001', 'tgw20260101000000002'}


def test_resolve_exact_sku():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', location='A1')
        make_item(root, 'tgw20260101000000002', location='A2')
        cfg = make_cfg(root)
        result = resolve(cfg, sku='tgw20260101000000001')
        assert result == {'tgw20260101000000001'}


def test_resolve_date_range():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001')
        make_item(root, 'tgw20260601000000001')
        cfg = make_cfg(root)
        result = resolve(cfg, date_from='20260101', date_to='20260131')
        assert result == {'tgw20260101000000001'}


def test_resolve_status():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', **{'#STATUS': 'ACTIVE'})
        make_item(root, 'tgw20260101000000002', **{'#STATUS': 'SOLD'})
        cfg = make_cfg(root)
        result = resolve(cfg, status='ACTIVE')
        assert result == {'tgw20260101000000001'}


def test_resolve_empty_location():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', location='A1')
        cfg = make_cfg(root)
        result = resolve(cfg, location='NOWHERE')
        assert result == set()


def test_resolve_combined_selectors():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001',
                  location='A1', **{'#STATUS': 'ACTIVE'})
        make_item(root, 'tgw20260101000000002',
                  location='A1', **{'#STATUS': 'SOLD'})
        cfg = make_cfg(root)
        result = resolve(cfg, status='ACTIVE', search='A1')
        assert result == {'tgw20260101000000001'}
