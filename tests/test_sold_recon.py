"""PP-SOLD-001 — tests for the token-free sold-reconciliation logic.

These functions decide whether catalog items get flagged sold/available, so a
regression means double-selling or phantom inventory. None of them hit eBay or
need a token; everything here runs offline against tmp_path fixtures.

Covered:
  * pull.build_listing_index  — indexes ebay_listing.listing_id AND legacy "Item number"
  * pull.build_title_lookup + pull.find_title_match — Jaccard match, threshold, tie-reject
  * pull.mark_item_sold       — idempotency, ebay_sale block, dry-run no-write
  * notifications.parse_sold_notification     — Transaction parse vs ping/test -> None
  * notifications.verify_notification_signature — MD5 check + deliberate accept-when-unverifiable

NOTE on verify_notification_signature: the current code intentionally ACCEPTS
(returns True) when there is no SOAP header, no signature, or no dev_id in
credentials — see the module docstring ("omitted -> signature check is skipped").
The tests below encode that as the deliberate contract, not as a bug.
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
def _silence_log_event(monkeypatch):
    # log_event may write to a configured sink; isolate it.
    monkeypatch.setattr(pull.tgw_logging, "log_event", lambda *a, **k: None)


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
    assert pull.mark_item_sold(p, cfg={}, **_SOLD_ARGS) is True

    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["status"] == "sold"
    assert doc["ebay_listing"]["status"] == "Sold"
    assert doc["ebay_sale"] == {
        "order_id": "O-1", "buyer": "bob", "sale_price": 19.99,
        "quantity": 1, "sale_date": "2026-06-07",
        "synced_at": "2026-06-07T00:00:00Z",
    }


def test_mark_item_sold_is_idempotent(tmp_path):
    p = _sold_item(tmp_path)
    assert pull.mark_item_sold(p, cfg={}, **_SOLD_ARGS) is True
    # Second call: already sold -> False, no change.
    assert pull.mark_item_sold(p, cfg={}, **dict(_SOLD_ARGS, order_id="O-2")) is False
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["ebay_sale"]["order_id"] == "O-1"  # not overwritten


def test_mark_item_sold_dry_run_does_not_write(tmp_path):
    p = _sold_item(tmp_path)
    assert pull.mark_item_sold(p, cfg={}, dry_run=True, **_SOLD_ARGS) is True
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["status"] == "available"
    assert "ebay_sale" not in doc


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
# verify_notification_signature (deliberate accept-when-unverifiable)
# ---------------------------------------------------------------------------

def _creds_cfg(tmp_path, **creds):
    p = tmp_path / "ebay-credentials.json"
    p.write_text(json.dumps(creds), encoding="utf-8")
    return {"ebay_credentials_path": p}


def test_verify_signature_accepts_when_no_header(tmp_path):
    # No SOAP header at all -> unverifiable -> deliberately accepted.
    assert notifications.verify_notification_signature(
        _soap(header_sig=None), _creds_cfg(tmp_path, dev_id="D", app_id="A", cert_id="C")
    ) is True


def test_verify_signature_accepts_when_no_signature(tmp_path):
    # Header present but empty signature -> accepted.
    assert notifications.verify_notification_signature(
        _soap(header_sig=""), _creds_cfg(tmp_path, dev_id="D", app_id="A", cert_id="C")
    ) is True


def test_verify_signature_accepts_when_dev_id_missing(tmp_path):
    # Signature present but no dev_id in creds -> cannot verify -> accepted (warned).
    assert notifications.verify_notification_signature(
        _soap(header_sig="deadbeef"), _creds_cfg(tmp_path, app_id="A", cert_id="C")
    ) is True


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
