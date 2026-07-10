"""Tests for tgw.history_index — GEMINI-007 / PP-HISTORY-001."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tgw.history_index import (
    _load_ebay_indexed_skus,
    _load_existing_skus,
    index_archive_unindexed,
    index_loose_csvs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    itemdata_root = tmp_path / "data" / "ItemData"
    itemdata_root.mkdir(parents=True)
    return {
        "itemdata_root": itemdata_root,
    }


def _make_zip(archive_dir: Path, sku: str, data: dict) -> Path:
    """Create an ItemArchive-format zip with sku.json inside."""
    zip_path = archive_dir / f"{sku}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{sku}.json", json.dumps(data))
    return zip_path


# ---------------------------------------------------------------------------
# _load_existing_skus
# ---------------------------------------------------------------------------


def test_load_existing_skus_empty_file(tmp_path):
    p = tmp_path / "idx.jsonl"
    assert _load_existing_skus(p) == set()


def test_load_existing_skus_reads_skus(tmp_path):
    p = tmp_path / "idx.jsonl"
    p.write_text(
        json.dumps({"sku": "tgw001"}) + "\n" + json.dumps({"sku": "tgw002"}) + "\n",
        encoding="utf-8",
    )
    assert _load_existing_skus(p) == {"tgw001", "tgw002"}


# ---------------------------------------------------------------------------
# _load_ebay_indexed_skus
# ---------------------------------------------------------------------------


def test_load_ebay_indexed_skus_reads_values(tmp_path):
    idx = tmp_path / "archive-ebay-index.json"
    idx.write_text(json.dumps({"12345678": "tgwAAA", "99999999": "tgwBBB"}), encoding="utf-8")
    result = _load_ebay_indexed_skus(idx)
    assert result == {"tgwAAA", "tgwBBB"}


def test_load_ebay_indexed_skus_missing_file(tmp_path):
    assert _load_ebay_indexed_skus(tmp_path / "nonexistent.json") == set()


# ---------------------------------------------------------------------------
# index_archive_unindexed
# ---------------------------------------------------------------------------


def _setup_archive(cfg, tmp_path, zips_data: dict, ebay_index: dict = None, existing_index_lines=None):
    """Build an ItemArchive directory + supporting index files."""
    history_dir = cfg["itemdata_root"].parent / "history"
    archive_dir = history_dir / "ItemArchive"
    archive_dir.mkdir(parents=True)
    var_dir = cfg["itemdata_root"].parent.parent / "var"
    var_dir.mkdir(parents=True)

    for sku, data in zips_data.items():
        _make_zip(archive_dir, sku, data)

    ebay_idx_path = var_dir / "archive-ebay-index.json"
    ebay_idx_path.write_text(json.dumps(ebay_index or {}), encoding="utf-8")

    out_path = var_dir / "history-itemdata-index.jsonl"
    if existing_index_lines:
        out_path.write_text("\n".join(existing_index_lines) + "\n", encoding="utf-8")

    return archive_dir, ebay_idx_path, out_path


def test_index_archive_dry_run_no_write(cfg, tmp_path):
    archive_dir, ebay_idx, out_path = _setup_archive(cfg, tmp_path, {
        "tgw001": {"sku": "tgw001", "title": "Test Item", "#STATUS": "unknown", "#LOCATION": "A1"},
    })
    stats = index_archive_unindexed(cfg, out_path=out_path, archive_ebay_index_path=ebay_idx, dry_run=True)
    assert stats["new"] == 1
    assert stats["dry_run"] is True
    assert not out_path.exists() or out_path.read_text() == ""


def test_index_archive_writes_new_records(cfg, tmp_path):
    archive_dir, ebay_idx, out_path = _setup_archive(cfg, tmp_path, {
        "tgw001": {"sku": "tgw001", "title": "Test Item", "#LOCATION": "A1", "#STATUS": "unknown"},
        "tgw002": {"sku": "tgw002", "title": "Another", "#LOCATION": "B2", "#STATUS": "unknown"},
    })
    stats = index_archive_unindexed(cfg, out_path=out_path, archive_ebay_index_path=ebay_idx, dry_run=False)
    assert stats["new"] == 2
    lines = [json.loads(row) for row in out_path.read_text().splitlines() if row.strip()]
    assert len(lines) == 2
    assert lines[0]["sku"] == "tgw001"
    assert lines[0]["location"] == "A1"


def test_index_archive_skips_ebay_indexed(cfg, tmp_path):
    archive_dir, ebay_idx, out_path = _setup_archive(
        cfg, tmp_path,
        {"tgw001": {"sku": "tgw001", "title": "Has eBay"}, "tgw002": {"sku": "tgw002", "title": "No eBay"}},
        ebay_index={"111111111111": "tgw001"},
    )
    stats = index_archive_unindexed(cfg, out_path=out_path, archive_ebay_index_path=ebay_idx, dry_run=False)
    assert stats["already_ebay"] == 1
    assert stats["new"] == 1


def test_index_archive_skips_already_indexed(cfg, tmp_path):
    existing = [json.dumps({"sku": "tgw001", "title": "Old"})]
    archive_dir, ebay_idx, out_path = _setup_archive(
        cfg, tmp_path,
        {"tgw001": {"sku": "tgw001", "title": "Dup"}, "tgw002": {"sku": "tgw002", "title": "New"}},
        existing_index_lines=existing,
    )
    stats = index_archive_unindexed(cfg, out_path=out_path, archive_ebay_index_path=ebay_idx, dry_run=False)
    assert stats["already_indexed"] == 1
    assert stats["new"] == 1


def test_index_archive_skips_no_json(cfg, tmp_path):
    history_dir = cfg["itemdata_root"].parent / "history"
    archive_dir = history_dir / "ItemArchive"
    archive_dir.mkdir(parents=True)
    var_dir = cfg["itemdata_root"].parent.parent / "var"
    var_dir.mkdir(parents=True)
    ebay_idx = var_dir / "archive-ebay-index.json"
    ebay_idx.write_text("{}", encoding="utf-8")
    out_path = var_dir / "history-itemdata-index.jsonl"

    # Create zip with no JSON inside
    zip_path = archive_dir / "tgw999.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo.jpg", b"fake-image")

    stats = index_archive_unindexed(cfg, out_path=out_path, archive_ebay_index_path=ebay_idx, dry_run=False)
    assert stats["skipped_no_json"] == 1
    assert stats["new"] == 0


def test_index_archive_limit(cfg, tmp_path):
    archive_dir, ebay_idx, out_path = _setup_archive(cfg, tmp_path, {
        f"tgw{i:03d}": {"sku": f"tgw{i:03d}", "title": f"Item {i}"} for i in range(10)
    })
    stats = index_archive_unindexed(cfg, out_path=out_path, archive_ebay_index_path=ebay_idx, dry_run=False, limit=3)
    assert stats["new"] == 3


# ---------------------------------------------------------------------------
# index_loose_csvs
# ---------------------------------------------------------------------------

# Minimal eBay OrdersReport CSV — only the columns that matter for indexing
_ORDERS_HEADER = (
    "Sales Record Number,Order Number,Buyer Username,Buyer Name,Buyer Email,"
    "Buyer Note,Buyer Address 1,Buyer Address 2,Buyer City,Buyer State,Buyer Zip,"
    "Buyer Country,Ship To Name,Ship To Phone,Ship To Address 1,Ship To Address 2,"
    "Ship To City,Ship To State,Ship To Zip,Ship To Country,Item Number,Item Title,"
    "Custom Label,Sold Via Promoted Listings,Quantity,Sold For,Shipping And Handling,"
    "Seller Collected Tax,eBay Collected Tax,Total Price,eBay Collected Tax Included in Total,"
    "Payment Method,Sale Date,Paid On Date,Ship By Date,Minimum Estimated Delivery Date,"
    "Maximum Estimated Delivery Date,Shipped On Date,Feedback Left,Feedback Received,"
    "My Item Note,PayPal Transaction ID,Shipping Service,Tracking Number,Transaction ID,"
    "Variation Details,Global Shipping Program,Global Shipping Reference ID,"
    "Click And Collect,Click And Collect Reference Number,eBay Plus"
)
_ORDERS_ROW = (
    "1,1-01,buyer1,John Doe,a@b.com,,123 St,,City,CA,90210,US,John Doe,,123 St,,"
    "City,CA,90210,US,123456789012,Cool Widget,tgwSKU001,No,1,9.99,3.99,,,,PayPal,"
    "2020-01-15,2020-01-15,2020-01-17,,,2020-01-16,,,,,USPS Ground,1Z999,t1,,No,,,No"
)
_ORDERS_CSV = _ORDERS_HEADER + "\n" + _ORDERS_ROW + "\n"


def test_index_loose_csvs_dry_run(tmp_path):
    history_root = tmp_path / "history"
    history_root.mkdir()
    (history_root / "eBay-OrdersReport-Jan-2020.csv").write_text(_ORDERS_CSV, encoding="utf-8")
    var_dir = tmp_path / "var"
    var_dir.mkdir()
    out_path = var_dir / "history-loose-csv-index.jsonl"

    cfg = {"itemdata_root": tmp_path / "data" / "ItemData"}
    (tmp_path / "data" / "ItemData").mkdir(parents=True)

    stats = index_loose_csvs(cfg, out_path=out_path, history_root=history_root, dry_run=True)
    assert stats["records"] == 1
    assert stats["dry_run"] is True
    assert not out_path.exists()


def test_index_loose_csvs_writes_records(tmp_path):
    history_root = tmp_path / "history"
    history_root.mkdir()
    (history_root / "eBay-OrdersReport-Jan-2020.csv").write_text(_ORDERS_CSV, encoding="utf-8")
    var_dir = tmp_path / "var"
    var_dir.mkdir()
    out_path = var_dir / "history-loose-csv-index.jsonl"

    cfg = {"itemdata_root": tmp_path / "data" / "ItemData"}
    (tmp_path / "data" / "ItemData").mkdir(parents=True)

    stats = index_loose_csvs(cfg, out_path=out_path, history_root=history_root, dry_run=False)
    assert stats["records"] == 1
    lines = [json.loads(row) for row in out_path.read_text().splitlines() if row.strip()]
    assert lines[0]["ebay_id"] == "123456789012"
    assert lines[0]["sku"] == "tgwSKU001"
    assert lines[0]["sold_for"] == "9.99"


def test_index_loose_csvs_no_csvs(tmp_path):
    history_root = tmp_path / "history"
    history_root.mkdir()
    cfg = {"itemdata_root": tmp_path / "data" / "ItemData"}
    (tmp_path / "data" / "ItemData").mkdir(parents=True)

    stats = index_loose_csvs(cfg, history_root=history_root, dry_run=True)
    assert stats["records"] == 0
    assert stats["files_scanned"] == 0
