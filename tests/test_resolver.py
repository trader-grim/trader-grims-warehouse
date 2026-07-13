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


def test_resolve_old_format_partial_sku_prefix_match():
    # A 14-17 char query that isn't itself a directory should still match
    # full-length SKUs sharing that prefix (old-format prefix-match fast
    # path). Regression test for #1285: comparing s[:18] to q[:18] never
    # matched because slicing a <18-char query to [:18] is a no-op, so the
    # two operands ended up different lengths and could never be equal.
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', location='A1')
        make_item(root, 'tgw20260601000000002', location='A2')
        cfg = make_cfg(root)

        # 17 chars: 'tgw' + 14 digits
        partial = 'tgw20260101000000'[:17]
        assert 14 <= len(partial) <= 17
        result = resolve(cfg, sku=partial)
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


def test_resolve_empty_field_missing_or_null():
    """Items where the field is absent or None match; items with a value do not."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001')                  # location absent
        make_item(root, 'tgw20260101000000002', location=None)   # location null
        make_item(root, 'tgw20260101000000003', location='A1')   # has a location
        cfg = make_cfg(root)
        result = resolve(cfg, empty_field='location')
        assert 'tgw20260101000000001' in result
        assert 'tgw20260101000000002' in result
        assert 'tgw20260101000000003' not in result


def test_resolve_empty_field_empty_string():
    """Items where the field is an empty or whitespace string also match."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', location='')    # empty string
        make_item(root, 'tgw20260101000000002', location='  ')  # whitespace
        make_item(root, 'tgw20260101000000003', location='B2')  # non-empty
        cfg = make_cfg(root)
        result = resolve(cfg, empty_field='location')
        assert 'tgw20260101000000001' in result
        assert 'tgw20260101000000002' in result
        assert 'tgw20260101000000003' not in result


def test_resolve_corrupt_item_json_skipped_but_logged(caplog):
    """A JSON-loading selector must not crash or silently drop a corrupt item —
    it should skip the corrupt SKU, still return the valid matches, and leave
    a WARNING log record naming the SKU. Regression test for #1301: resolve()
    previously caught the load failure in a bare `except Exception: continue`
    with no trace left anywhere."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        make_item(root, 'tgw20260101000000001', **{'#STATUS': 'ACTIVE'})
        make_item(root, 'tgw20260101000000002', **{'#STATUS': 'ACTIVE'})

        # Corrupt the second item's JSON so load_item_doc_by_sku() raises.
        corrupt_path = root / 'tgw20260101000000002' / 'tgw20260101000000002.json'
        corrupt_path.write_text('{not valid json', encoding='utf-8')

        cfg = make_cfg(root)
        with caplog.at_level('WARNING', logger='tgw.resolver'):
            result = resolve(cfg, status='ACTIVE')

        # Valid item still found; search doesn't crash or silently vanish
        # the whole result set.
        assert result == {'tgw20260101000000001'}

        # A warning was logged naming the skipped SKU.
        warnings = [r for r in caplog.records if r.levelname == 'WARNING']
        assert any('tgw20260101000000002' in r.getMessage() for r in warnings)
