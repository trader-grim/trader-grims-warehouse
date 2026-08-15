"""PP-SOLD-001 — tests for the token-free sold-reconciliation logic.

These functions decide whether catalog items get flagged sold/available, so a
regression means double-selling or phantom inventory. None of them hit eBay or
need a token; everything here runs offline against tmp_path fixtures.

Covered:
  * pull.build_listing_index  — indexes ebay_listing.listing_id AND legacy "Item number"
  * pull.build_title_lookup + pull.find_title_match — Jaccard match, threshold, tie-reject
  * pull.mark_item_sold       — idempotency, ebay_sale block, dry-run no-write
  * notifications.parse_sold_notification     — Transaction parse vs ping/test -> None
  * notifications.verify_notification_signature — MD5 check, fails CLOSED on any
    unverifiable case (audit#1143 / todo #1174: this endpoint is public and
    unauthenticated, so a fail-open here let an attacker forge sold notifications)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import tgw.apis.ebay.notifications as notifications
import tgw.ebay.pull as pull
from tgw.apis.ebay.trading import _NS

_SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"


# ---------------------------------------------------------------------------
# build_listing_index
# ---------------------------------------------------------------------------

def _write_item(root: Path, sku: str, doc: dict) -> None:
    d = root / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_build_listing_index_indexes_pipeline_and_legacy_ids(tmp_path):
    _write_item(tmp_path, "tgw001", {"ebay_listing": {"listing_id": "111"}})
    _write_item(tmp_path, "tgw002", {"Item number": "222"})
    _write_item(tmp_path, "tgw003", {"title": "no ids here"})  # excluded

    index = pull.build_listing_index(tmp_path)

    assert index["111"] == tmp_path / "tgw001" / "tgw001.json"
    assert index["222"] == tmp_path / "tgw002" / "tgw002.json"
    assert "tgw003" not in {p.parent.name for p in index.values()}
    assert len(index) == 2


def test_build_listing_index_pipeline_id_wins_over_legacy(tmp_path):
    # An item carrying both keys is indexed under both ids, same path.
    _write_item(tmp_path, "tgw010",
                {"ebay_listing": {"listing_id": "900"}, "Item number": "901"})
    index = pull.build_listing_index(tmp_path)
    assert index["900"] == index["901"] == tmp_path / "tgw010" / "tgw010.json"


# ---------------------------------------------------------------------------
# find_title_match (+ build_title_lookup)
# ---------------------------------------------------------------------------

def _indexes(titles: dict):
    """Build (title_index, word_index) the way build_title_lookup does."""
    title_index: dict = {}
    word_index: dict = {}
    for sku, title in titles.items():
        tokens = pull._tokenize(title)
        key = " ".join(sorted(tokens))
        if key not in title_index:
            title_index[key] = (sku, Path(f"/x/{sku}/{sku}.json"))
            for w in tokens:
                word_index.setdefault(w, []).append(key)
    return title_index, word_index


def test_find_title_match_exact_match():
    ti, wi = _indexes({"tgw001": "alpha bravo charlie delta echo"})
    match = pull.find_title_match("alpha bravo charlie delta echo", ti, wi)
    assert match is not None
    sku, _path, score = match
    assert sku == "tgw001"
    assert score == 1.0


def test_find_title_match_above_threshold_unique():
    # 4 of 5 query tokens shared -> Jaccard 4/5 = 0.80 == default threshold.
    ti, wi = _indexes({"tgw001": "alpha bravo charlie delta"})
    match = pull.find_title_match("alpha bravo charlie delta echo", ti, wi)
    assert match is not None and match[0] == "tgw001"
    assert match[2] == pytest.approx(0.8)


def test_find_title_match_below_threshold_returns_none():
    ti, wi = _indexes({"tgw001": "alpha bravo"})  # 2/5 = 0.4
    assert pull.find_title_match("alpha bravo charlie delta echo", ti, wi) is None


def test_find_title_match_tie_is_rejected():
    # Two distinct candidates both scoring exactly 0.80 -> ambiguous -> None.
    ti, wi = _indexes({
        "tgw001": "alpha bravo charlie delta",
        "tgw002": "alpha bravo charlie echo",
    })
    assert pull.find_title_match("alpha bravo charlie delta echo", ti, wi) is None


def test_find_title_match_no_candidates_returns_none():
    ti, wi = _indexes({"tgw001": "completely different words here"})
    assert pull.find_title_match("alpha bravo charlie", ti, wi) is None


def test_find_title_match_empty_query_returns_none():
    ti, wi = _indexes({"tgw001": "alpha bravo charlie delta echo"})
    # All-stopword / too-short query tokenizes to nothing.
    assert pull.find_title_match("a an it", ti, wi) is None


def test_build_title_lookup_then_match(tmp_path):
    # End-to-end: real SQLite catalog -> indexes -> match resolves the sku.
    import sqlite3
    db = tmp_path / "catalog.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE catalog (sku TEXT, title TEXT)")
    con.executemany(
        "INSERT INTO catalog (sku, title) VALUES (?, ?)",
        [
            ("tgw100", "vintage scarlet gizmo deluxe edition"),
            ("tgw200", "blue ordinary household gadget thing"),
            ("tgw300", "tgw300"),   # title == sku -> skipped by build_title_lookup
        ],
    )
    con.commit()
    con.close()

    title_index, word_index = pull.build_title_lookup(db, tmp_path)
    match = pull.find_title_match(
        "vintage scarlet gizmo deluxe edition", title_index, word_index)
    assert match is not None and match[0] == "tgw100"


# ---------------------------------------------------------------------------
# mark_item_sold
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _silence_log_event(tmp_path, monkeypatch):
    # log_event may write to a configured sink; isolate it.
    monkeypatch.setattr(pull.tgw_logging, "log_event", lambda *a, **k: None)
    from tests.conftest import make_fake_fence_write_tmp, make_fake_patch_item_tmp
    monkeypatch.setattr(pull, 'fence_ebay_write', make_fake_fence_write_tmp(tmp_path))
    monkeypatch.setattr(pull, 'fence_patch_item', make_fake_patch_item_tmp(tmp_path))


def _sold_item(tmp_path, sku="tgw500", **doc):
    base = {"sku": sku, "title": "Thing", "status": "available"}
    base.update(doc)
    d = tmp_path / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sku}.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


_SOLD_ARGS = dict(order_id="O-1", buyer="bob", sale_price=19.99,
                  quantity=1, sale_date="2026-06-07", synced_at="2026-06-07T00:00:00Z")


def test_mark_item_sold_writes_sale_block(tmp_path):
    p = _sold_item(tmp_path)
    assert pull.mark_item_sold(p, cfg={"api_key": "test-api-key"}, **_SOLD_ARGS) is True

    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["status"] == "sold"
    assert doc["ebay_listing"]["status"] == "Sold"
    # ebay_sale is a LIST of sold-order records (todo #1604 / PP-SOLD-001).
    assert doc["ebay_sale"] == [{
        "order_id": "O-1", "buyer": "bob", "sale_price": 19.99,
        "quantity": 1, "sale_date": "2026-06-07",
        "synced_at": "2026-06-07T00:00:00Z",
    }]


def test_mark_item_sold_same_order_id_is_idempotent(tmp_path):
    # Re-delivering the SAME order_id must not duplicate the record.
    p = _sold_item(tmp_path)
    assert pull.mark_item_sold(p, cfg={"api_key": "test-api-key"}, **_SOLD_ARGS) is True
    assert pull.mark_item_sold(p, cfg={"api_key": "test-api-key"}, **_SOLD_ARGS) is False
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert len(doc["ebay_sale"]) == 1
    assert doc["ebay_sale"][0]["order_id"] == "O-1"


def test_mark_item_sold_second_distinct_order_is_never_dropped(tmp_path):
    # Regression test for todo #1604 / PP-SOLD-001: a genuinely different
    # order_id for a SKU that is already status=sold must still be recorded
    # (appended), not silently discarded by the old status=='sold' guard.
    p = _sold_item(tmp_path)
    assert pull.mark_item_sold(p, cfg={"api_key": "test-api-key"}, **_SOLD_ARGS) is True
    second_args = dict(_SOLD_ARGS, order_id="O-2", buyer="alice",
                        sale_date="2026-06-08", synced_at="2026-06-08T00:00:00Z")
    assert pull.mark_item_sold(p, cfg={"api_key": "test-api-key"}, **second_args) is True

    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["status"] == "sold"
    assert len(doc["ebay_sale"]) == 2
    order_ids = {rec["order_id"] for rec in doc["ebay_sale"]}
    assert order_ids == {"O-1", "O-2"}
    second_rec = next(r for r in doc["ebay_sale"] if r["order_id"] == "O-2")
    assert second_rec["buyer"] == "alice"


def test_mark_item_sold_dry_run_does_not_write(tmp_path):
    p = _sold_item(tmp_path)
    assert pull.mark_item_sold(p, cfg={"api_key": "test-api-key"}, dry_run=True, **_SOLD_ARGS) is True
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["status"] == "available"
    assert "ebay_sale" not in doc


def _order(order_id: str, listing_id: str, *, quantity: int = 1) -> dict:
    return {
        "order_id": order_id,
        "buyer": "buyer",
        "transactions": [{
            "listing_id": listing_id,
            "sale_price": 12.5,
            "quantity": quantity,
            "sale_date": "2026-08-01T00:00:00Z",
        }],
    }


def test_load_sold_order_csvs_handles_blank_prefix_and_real_headers(tmp_path):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(
        '\ufeff,,,,\n'
        '"Order Number","Buyer Username","Item Number","Quantity",'
        '"Sold For","Sale Date"\n'
        '"O-CSV","alice","L-CSV","2","$12.50","2026-05-01"\n',
        encoding="utf-8",
    )

    orders = pull.load_sold_order_csvs([csv_path])

    assert orders == [{
        "order_id": "O-CSV",
        "buyer": "alice",
        "created_at": "2026-05-01",
        "transactions": [{
            "listing_id": "L-CSV",
            "sku": "",
            "sale_price": 12.5,
            "quantity": 2,
            "sale_date": "2026-05-01",
        }],
    }]


def test_history_reconcile_deduplicates_api_and_csv_order(tmp_path):
    _write_item(tmp_path, "tgw-dedupe", {
        "sku": "tgw-dedupe",
        "status": "In Stock",
        "draft_listing": {"quantity": 3},
        "ebay_listing": {"listing_id": "L-D", "status": "Active"},
    })
    order = _order("O-D", "L-D", quantity=1)

    result = pull.reconcile_sold_order_history(
        {"itemdata_root": tmp_path, "api_key": "test-api-key"},
        tmp_path,
        [order, order],
        [{"listing_id": "L-D", "status": "Active", "quantity": 3}],
        "2026-08-15T00:00:00Z",
        dry_run=False,
    )

    item = json.loads((tmp_path / "tgw-dedupe" / "tgw-dedupe.json").read_text())
    assert result["active_sales_recorded"] == 1
    assert len(item["ebay_sale"]) == 1
    assert item["draft_listing"]["quantity"] == 3


def test_history_reconcile_rejects_conflicting_duplicate_quantity(tmp_path):
    with pytest.raises(ValueError, match="conflicting quantity"):
        pull.reconcile_sold_order_history(
            {"itemdata_root": tmp_path, "api_key": "test-api-key"},
            tmp_path,
            [_order("O-X", "L-X", quantity=1),
             _order("O-X", "L-X", quantity=2)],
            [],
            "2026-08-15T00:00:00Z",
        )


def test_history_reconcile_old_listing_csv_does_not_sell_active_relisted_sku(
    tmp_path,
):
    _write_item(tmp_path, "tgw123", {
        "sku": "tgw123",
        "status": "In Stock",
        "draft_listing": {"quantity": 2},
        "ebay_listing": {"listing_id": "L-NEW", "status": "Active"},
    })
    historic = _order("O-OLD", "L-OLD")
    historic["transactions"][0]["sku"] = "tgw123"

    result = pull.reconcile_sold_order_history(
        {"itemdata_root": tmp_path, "api_key": "test-api-key"},
        tmp_path,
        [historic],
        [{
            "listing_id": "L-NEW", "custom_label": "tgw123",
            "status": "Active", "quantity": 2,
        }],
        "2026-08-15T00:00:00Z",
        dry_run=False,
    )

    item = json.loads((tmp_path / "tgw123" / "tgw123.json").read_text())
    assert result["active_sales_recorded"] == 1
    assert item["status"] == "In Stock"
    assert item["draft_listing"]["quantity"] == 2
    assert item["ebay_sale"][0]["order_id"] == "O-OLD"


def test_history_reconcile_active_custom_label_resolves_missing_listing_index(
    tmp_path,
):
    _write_item(tmp_path, "tgw456", {
        "sku": "tgw456",
        "status": "sold",
        "draft_listing": {"quantity": 0},
    })

    result = pull.reconcile_sold_order_history(
        {"itemdata_root": tmp_path, "api_key": "test-api-key"},
        tmp_path,
        [],
        [{
            "listing_id": "L-ACTIVE", "custom_label": "tgw456",
            "status": "Active", "quantity": 1,
        }],
        "2026-08-15T00:00:00Z",
        dry_run=False,
    )

    item = json.loads((tmp_path / "tgw456" / "tgw456.json").read_text())
    assert result["active_unmatched"] == []
    assert result["active_status_restored"] == 0
    assert result["ok"] is False
    assert result["active_sold_conflicts"] == [{
        "sku": "tgw456",
        "listing_id": "L-ACTIVE",
        "provider_available_quantity": 1,
        "prior_sold_marker": False,
    }]
    assert item["status"] == "sold"
    assert item["draft_listing"]["quantity"] == 0


def test_history_reconcile_holds_provider_active_sold_inventory(tmp_path):
    _write_item(tmp_path, "tgw-active", {
        "sku": "tgw-active",
        "status": "sold",
        "draft_listing": {"quantity": 0},
        "ebay_listing": {"listing_id": "L-1", "status": "Sold"},
        "ebay_sale": [{"order_id": "OLD"}],
    })

    result = pull.reconcile_sold_order_history(
        {"itemdata_root": tmp_path, "api_key": "test-api-key"},
        tmp_path,
        [_order("NEW", "L-1")],
        [{
            "listing_id": "L-1", "custom_label": "tgw-active",
            "status": "Active", "quantity": 4, "quantity_sold": 0,
        }],
        "2026-08-15T00:00:00Z",
        dry_run=False,
    )

    item = json.loads((tmp_path / "tgw-active" / "tgw-active.json").read_text())
    assert result["ok"] is False
    assert item["ebay_listing"]["status"] == "Sold"
    assert result["active_status_restored"] == 0
    assert len(result["active_sold_conflicts"]) == 1
    assert result["active_sales_recorded"] == 1
    assert item["status"] == "sold"
    assert item["draft_listing"]["quantity"] == 0
    assert {sale["order_id"] for sale in item["ebay_sale"]} == {"OLD", "NEW"}


def test_history_reconcile_repairs_v1_sold_marker_without_relisting(tmp_path):
    _write_item(tmp_path, "tgw-repair", {
        "sku": "tgw-repair",
        "status": "In Stock",
        "draft_listing": {"quantity": 4, "title": "preserved"},
        "ebay_listing": {"listing_id": "L-REPAIR", "status": "Active"},
        "sold_reconciliation": {
            "schema": "tgw-sold-active-reconciliation/v1",
        },
    })

    result = pull.reconcile_sold_order_history(
        {"itemdata_root": tmp_path, "api_key": "test-api-key"},
        tmp_path,
        [],
        [{
            "listing_id": "L-REPAIR", "custom_label": "tgw-repair",
            "status": "Active", "quantity": 4,
        }],
        "2026-08-15T00:00:00Z",
        dry_run=False,
    )

    item = json.loads((tmp_path / "tgw-repair" / "tgw-repair.json").read_text())
    assert result["ok"] is False
    assert result["active_sold_local_restored"] == 1
    assert item["status"] == "sold"
    assert item["draft_listing"] == {"quantity": 0, "title": "preserved"}
    assert item["ebay_listing"]["status"] == "Active"
    assert item["sold_reconciliation"]["schema"] == "tgw-sold-active-conflict/v2"


def test_history_reconcile_active_sale_does_not_decrement_provider_quantity(tmp_path):
    _write_item(tmp_path, "tgw-multi", {
        "sku": "tgw-multi",
        "status": "In Stock",
        "draft_listing": {"quantity": 7},
        "ebay_listing": {"listing_id": "L-2", "status": "Active"},
    })

    pull.reconcile_sold_order_history(
        {"itemdata_root": tmp_path, "api_key": "test-api-key"},
        tmp_path,
        [_order("ORDER", "L-2", quantity=2)],
        [{
            "listing_id": "L-2", "custom_label": "tgw-multi",
            "status": "Active", "quantity": 7, "quantity_sold": 0,
        }],
        "2026-08-15T00:00:00Z",
        dry_run=False,
    )

    item = json.loads((tmp_path / "tgw-multi" / "tgw-multi.json").read_text())
    assert item["status"] == "In Stock"
    assert item["draft_listing"]["quantity"] == 7
    assert item["ebay_sale"][0]["order_id"] == "ORDER"


def test_history_reconcile_inactive_completed_order_marks_sold(tmp_path):
    _write_item(tmp_path, "tgw-ended", {
        "sku": "tgw-ended",
        "status": "In Stock",
        "draft_listing": {"quantity": 1},
        "ebay_listing": {"listing_id": "L-3", "status": "UNPUBLISHED"},
    })

    result = pull.reconcile_sold_order_history(
        {"itemdata_root": tmp_path, "api_key": "test-api-key"},
        tmp_path,
        [_order("ORDER", "L-3")],
        [],
        "2026-08-15T00:00:00Z",
        dry_run=False,
    )

    item = json.loads((tmp_path / "tgw-ended" / "tgw-ended.json").read_text())
    assert result["inactive_sales_marked"] == 1
    assert item["status"] == "sold"
    assert item["draft_listing"]["quantity"] == 0


def test_history_reconcile_dry_run_leaves_active_sold_item_unchanged(tmp_path):
    _write_item(tmp_path, "tgw-dry", {
        "sku": "tgw-dry",
        "status": "sold",
        "draft_listing": {"quantity": 2},
        "ebay_listing": {"listing_id": "L-4", "status": "PUBLISHED"},
    })
    path = tmp_path / "tgw-dry" / "tgw-dry.json"
    before = path.read_bytes()

    result = pull.reconcile_sold_order_history(
        {"itemdata_root": tmp_path, "api_key": "test-api-key"},
        tmp_path,
        [_order("ORDER", "L-4")],
        [{
            "listing_id": "L-4", "custom_label": "tgw-dry",
            "status": "Active", "quantity": 2, "quantity_sold": 0,
        }],
        "2026-08-15T00:00:00Z",
        dry_run=True,
    )

    assert result["active_status_restored"] == 0
    assert len(result["active_sold_conflicts"]) == 1
    assert result["active_sales_recorded"] == 1
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# parse_sold_notification
# ---------------------------------------------------------------------------

def _soap(*, header_sig=None, timestamp="2026-06-07T00:00:00.000Z",
          transaction=True, item_id="123456789", price="19.99", qty="2",
          buyer="somebuyer", order_id="order-999", tx_id="tx-555"):
    header = ""
    if header_sig is not None:
        header = (
            f'  <soap:Header>\n'
            f'    <RequesterCredentials xmlns="{_NS}">\n'
            f'      <NotificationSignature>{header_sig}</NotificationSignature>\n'
            f'    </RequesterCredentials>\n'
            f'  </soap:Header>\n'
        )
    tx = ""
    if transaction:
        tx = (
            f'      <TransactionArray><Transaction>\n'
            f'        <Item><ItemID>{item_id}</ItemID></Item>\n'
            f'        <TransactionPrice>{price}</TransactionPrice>\n'
            f'        <QuantityPurchased>{qty}</QuantityPurchased>\n'
            f'        <CreatedDate>2026-06-07T00:00:00.000Z</CreatedDate>\n'
            f'        <Buyer><UserID>{buyer}</UserID></Buyer>\n'
            f'        <TransactionID>{tx_id}</TransactionID>\n'
            f'      </Transaction></TransactionArray>\n'
            f'      <OrderID>{order_id}</OrderID>\n'
        )
    xml = (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<soap:Envelope xmlns:soap="{_SOAP_NS}">\n'
        f'{header}'
        f'  <soap:Body>\n'
        f'    <GetItemTransactionsResponse xmlns="{_NS}">\n'
        f'      <Timestamp>{timestamp}</Timestamp>\n'
        f'{tx}'
        f'    </GetItemTransactionsResponse>\n'
        f'  </soap:Body>\n'
        f'</soap:Envelope>'
    )
    return xml.encode("utf-8")


def test_parse_sold_notification_extracts_fields():
    parsed = notifications.parse_sold_notification(_soap())
    assert parsed == {
        "listing_id": "123456789",
        "buyer":      "somebuyer",
        "sale_price": 19.99,
        "quantity":   2,
        "sale_date":  "2026-06-07T00:00:00.000Z",
        "order_id":   "order-999",
    }


def test_parse_sold_notification_ping_returns_none():
    # A SOAP body with no Transaction element is a ping/test, not a sale.
    assert notifications.parse_sold_notification(_soap(transaction=False)) is None


def test_parse_sold_notification_garbage_returns_none():
    assert notifications.parse_sold_notification(b"not xml at all") is None


# ---------------------------------------------------------------------------
# verify_notification_signature (fails CLOSED on any unverifiable case)
# ---------------------------------------------------------------------------

def _creds_cfg(tmp_path, **creds):
    p = tmp_path / "ebay-credentials.json"
    p.write_text(json.dumps(creds), encoding="utf-8")
    return {"ebay_credentials_path": p}


def test_verify_signature_rejects_when_no_header(tmp_path):
    # No SOAP header at all -> unverifiable -> rejected (audit#1143 / #1174).
    assert notifications.verify_notification_signature(
        _soap(header_sig=None), _creds_cfg(tmp_path, dev_id="D", app_id="A", cert_id="C")
    ) is False


def test_verify_signature_rejects_when_no_signature(tmp_path):
    # Header present but empty signature -> rejected.
    assert notifications.verify_notification_signature(
        _soap(header_sig=""), _creds_cfg(tmp_path, dev_id="D", app_id="A", cert_id="C")
    ) is False


def test_verify_signature_rejects_on_unparseable_body(tmp_path):
    # Garbage/non-XML payload -> parse exception -> rejected, not accepted.
    assert notifications.verify_notification_signature(
        b"not xml at all", _creds_cfg(tmp_path, dev_id="D", app_id="A", cert_id="C")
    ) is False


def test_verify_signature_rejects_when_dev_id_missing(tmp_path):
    # Signature present but no dev_id in creds -> cannot verify -> rejected.
    assert notifications.verify_notification_signature(
        _soap(header_sig="deadbeef"), _creds_cfg(tmp_path, app_id="A", cert_id="C")
    ) is False


def test_verify_signature_valid_md5_passes(tmp_path):
    ts = "2026-06-07T00:00:00.000Z"
    dev, app, cert = "DEV", "APP", "CERT"
    good = hashlib.md5((ts + dev + app + cert).encode("utf-8")).hexdigest()
    assert notifications.verify_notification_signature(
        _soap(header_sig=good, timestamp=ts),
        _creds_cfg(tmp_path, dev_id=dev, app_id=app, cert_id=cert),
    ) is True


def test_verify_signature_mismatch_fails(tmp_path):
    assert notifications.verify_notification_signature(
        _soap(header_sig="0" * 32, timestamp="2026-06-07T00:00:00.000Z"),
        _creds_cfg(tmp_path, dev_id="DEV", app_id="APP", cert_id="CERT"),
    ) is False
