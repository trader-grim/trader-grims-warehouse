"""Tests for tgw.offers + Trading API Best Offer functions (PP-OFFER-001).

All tests are offline — no eBay API calls.  trading_call is mocked to return
fake XML, and respond_to_best_offer is patched where live submission is tested.
"""

from __future__ import annotations

import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

import tgw.offers as offers_mod
from tgw.apis.ebay.trading import _NS, _offer_from_xml, get_best_offers, respond_to_best_offer
from tgw.offers import (
    _find_item_by_listing_id,
    _log_offer_history,
    _record_unresolved_offer,
    _resolve_unresolved_offer,
    cmd_offers_list,
    cmd_offers_respond,
)

# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

_T = f'{{{_NS}}}'


def _make_offer_xml(
    offer_id: str = "99001",
    listing_id: str = "12345678",
    title: str = "TGW Widget",
    sku: str = "tgw202601011200000",
    buyer: str = "buyer123",
    offer_price: str = "25.00",
    listing_price: str = "35.00",
    status: str = "Pending",
    expiry: str = "2026-06-20T00:00:00.000Z",
) -> ET.Element:
    """Build a <BestOffer> XML element with full namespace."""
    xml = f"""<BestOffer xmlns="{_NS}">
      <BestOfferID>{offer_id}</BestOfferID>
      <BestOfferStatus>{status}</BestOfferStatus>
      <ExpirationTime>{expiry}</ExpirationTime>
      <Buyer><UserID>{buyer}</UserID></Buyer>
      <Price currencyID="USD">{offer_price}</Price>
      <Item>
        <ItemID>{listing_id}</ItemID>
        <Title>{title}</Title>
        <SKU>{sku}</SKU>
        <SellingStatus>
          <CurrentPrice currencyID="USD">{listing_price}</CurrentPrice>
        </SellingStatus>
      </Item>
    </BestOffer>"""
    return ET.fromstring(xml)


def _make_get_best_offers_response(offers_xml: list[str], total_pages: int = 1) -> ET.Element:
    """Wrap offer elements in a GetBestOffersResponse envelope."""
    offers_block = '\n'.join(offers_xml)
    xml = f"""<GetBestOffersResponse xmlns="{_NS}">
      <Ack>Success</Ack>
      <PaginationResult>
        <TotalNumberOfPages>{total_pages}</TotalNumberOfPages>
        <TotalNumberOfEntries>{len(offers_xml)}</TotalNumberOfEntries>
      </PaginationResult>
      <BestOfferArray>
        {offers_block}
      </BestOfferArray>
    </GetBestOffersResponse>"""
    return ET.fromstring(xml)


def _offer_element_str(**kw) -> str:
    """Return an offer element as XML string (for embedding in response)."""
    offer = _make_offer_xml(**kw)
    return ET.tostring(offer, encoding="unicode")


def _make_empty_response() -> ET.Element:
    xml = f"""<GetBestOffersResponse xmlns="{_NS}">
      <Ack>Success</Ack>
      <PaginationResult>
        <TotalNumberOfPages>1</TotalNumberOfPages>
        <TotalNumberOfEntries>0</TotalNumberOfEntries>
      </PaginationResult>
    </GetBestOffersResponse>"""
    return ET.fromstring(xml)


def _make_respond_response() -> ET.Element:
    xml = f"""<RespondToBestOfferResponse xmlns="{_NS}">
      <Ack>Success</Ack>
    </RespondToBestOfferResponse>"""
    return ET.fromstring(xml)


# ---------------------------------------------------------------------------
# Test cfg helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, with_db: bool = False) -> dict:
    cfg = {
        "itemdata_root": tmp_path / "ItemData",
        "sqlite_catalog_path": str(tmp_path / "catalog.db"),
        "pretty": False,
    }
    if with_db:
        _init_db(tmp_path / "catalog.db")
    return cfg


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "CREATE TABLE catalog (sku TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )


def _insert_catalog(db_path: Path, sku: str, doc: dict) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO catalog (sku, data) VALUES (?, ?)", (sku, json.dumps(doc)))


def _write_item(itemdata_root: Path, sku: str, doc: dict) -> Path:
    d = itemdata_root / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _offer_from_xml
# ---------------------------------------------------------------------------

class TestOfferFromXml:
    def test_parses_all_fields(self):
        el = _make_offer_xml(
            offer_id="99001", listing_id="12345678", title="TGW Widget",
            sku="tgw202601011200000", buyer="buyer123",
            offer_price="25.00", listing_price="35.00",
            status="Pending", expiry="2026-06-20T00:00:00.000Z",
        )
        offer = _offer_from_xml(el)
        assert offer["offer_id"] == "99001"
        assert offer["listing_id"] == "12345678"
        assert offer["title"] == "TGW Widget"
        assert offer["sku"] == "tgw202601011200000"
        assert offer["buyer"] == "buyer123"
        assert offer["offer_price"] == pytest.approx(25.0)
        assert offer["listing_price"] == pytest.approx(35.0)
        assert offer["status"] == "Pending"
        assert offer["expiry"] == "2026-06-20T00:00:00.000Z"

    def test_missing_item_element_gives_empty_strings(self):
        xml = f'<BestOffer xmlns="{_NS}"><BestOfferID>1</BestOfferID><BestOfferStatus>Pending</BestOfferStatus></BestOffer>'
        el = ET.fromstring(xml)
        offer = _offer_from_xml(el)
        assert offer["listing_id"] == ""
        assert offer["title"] == ""
        assert offer["sku"] == ""
        assert offer["listing_price"] is None

    def test_missing_price_element_gives_none(self):
        xml = f'<BestOffer xmlns="{_NS}"><BestOfferID>2</BestOfferID></BestOffer>'
        el = ET.fromstring(xml)
        offer = _offer_from_xml(el)
        assert offer["offer_price"] is None

    def test_accepted_status(self):
        el = _make_offer_xml(status="Accepted")
        assert _offer_from_xml(el)["status"] == "Accepted"


# ---------------------------------------------------------------------------
# get_best_offers
# ---------------------------------------------------------------------------

class TestGetBestOffers:
    def test_yields_offers(self):
        offer_str = _offer_element_str(offer_id="99001")
        response = _make_get_best_offers_response([offer_str])
        with patch("tgw.apis.ebay.trading.trading_call", return_value=response):
            offers = list(get_best_offers({"token": "x"}, status="Pending"))
        assert len(offers) == 1
        assert offers[0]["offer_id"] == "99001"

    def test_passes_status_filter(self):
        response = _make_empty_response()
        with patch("tgw.apis.ebay.trading.trading_call", return_value=response) as mock_call:
            list(get_best_offers({"token": "x"}, status="All"))
        xml_sent = mock_call.call_args[0][2]
        assert "<BestOfferStatus>All</BestOfferStatus>" in xml_sent

    def test_passes_listing_id_when_given(self):
        response = _make_empty_response()
        with patch("tgw.apis.ebay.trading.trading_call", return_value=response) as mock_call:
            list(get_best_offers({"token": "x"}, listing_id="12345678"))
        xml_sent = mock_call.call_args[0][2]
        assert "<ItemID>12345678</ItemID>" in xml_sent

    def test_no_listing_id_omits_item_id_element(self):
        response = _make_empty_response()
        with patch("tgw.apis.ebay.trading.trading_call", return_value=response) as mock_call:
            list(get_best_offers({"token": "x"}))
        xml_sent = mock_call.call_args[0][2]
        assert "<ItemID>" not in xml_sent

    def test_empty_response_yields_nothing(self):
        response = _make_empty_response()
        with patch("tgw.apis.ebay.trading.trading_call", return_value=response):
            offers = list(get_best_offers({"token": "x"}))
        assert offers == []

    def test_yields_multiple_offers(self):
        offer_a = _offer_element_str(offer_id="1001", sku="tgw001")
        offer_b = _offer_element_str(offer_id="1002", sku="tgw002")
        response = _make_get_best_offers_response([offer_a, offer_b])
        with patch("tgw.apis.ebay.trading.trading_call", return_value=response):
            offers = list(get_best_offers({"token": "x"}))
        assert len(offers) == 2
        assert {o["offer_id"] for o in offers} == {"1001", "1002"}


# ---------------------------------------------------------------------------
# respond_to_best_offer
# ---------------------------------------------------------------------------

class TestRespondToBestOffer:
    def test_accept_sends_correct_xml(self):
        resp = _make_respond_response()
        with patch("tgw.apis.ebay.trading.trading_call", return_value=resp) as mock_call:
            respond_to_best_offer({"token": "x"}, offer_id="99001", listing_id="12345678", action="Accept")
        call_name = mock_call.call_args[0][1]
        xml_sent = mock_call.call_args[0][2]
        assert call_name == "RespondToBestOffer"
        assert "<Action>Accept</Action>" in xml_sent
        assert "<ItemID>12345678</ItemID>" in xml_sent
        assert "<BestOfferID>99001</BestOfferID>" in xml_sent
        assert "CounterOfferPrice" not in xml_sent

    def test_decline_sends_correct_action(self):
        resp = _make_respond_response()
        with patch("tgw.apis.ebay.trading.trading_call", return_value=resp) as mock_call:
            respond_to_best_offer({"token": "x"}, offer_id="99001", listing_id="12345678", action="Decline")
        xml_sent = mock_call.call_args[0][2]
        assert "<Action>Decline</Action>" in xml_sent

    def test_counter_includes_price(self):
        resp = _make_respond_response()
        with patch("tgw.apis.ebay.trading.trading_call", return_value=resp) as mock_call:
            respond_to_best_offer(
                {"token": "x"}, offer_id="99001", listing_id="12345678",
                action="Counter", counter_price=28.00,
            )
        xml_sent = mock_call.call_args[0][2]
        assert "<Action>Counter</Action>" in xml_sent
        assert "28.00" in xml_sent
        assert "CounterOfferPrice" in xml_sent

    def test_accept_without_counter_price_sends_no_counter_block(self):
        resp = _make_respond_response()
        with patch("tgw.apis.ebay.trading.trading_call", return_value=resp) as mock_call:
            respond_to_best_offer(
                {"token": "x"}, offer_id="99001", listing_id="12345678",
                action="Accept", counter_price=None,
            )
        xml_sent = mock_call.call_args[0][2]
        assert "CounterOfferPrice" not in xml_sent


# ---------------------------------------------------------------------------
# _find_item_by_listing_id
# ---------------------------------------------------------------------------

class TestFindItemByListingId:
    def test_finds_item(self, tmp_path):
        cfg = _make_cfg(tmp_path, with_db=True)
        doc = {"sku": "tgw001", "ebay_listing": {"listing_id": "12345678"}}
        _insert_catalog(tmp_path / "catalog.db", "tgw001", doc)
        _write_item(cfg["itemdata_root"], "tgw001", doc)

        path = _find_item_by_listing_id(cfg, "12345678")
        assert path is not None
        assert path.name == "tgw001.json"

    def test_returns_none_when_not_found(self, tmp_path):
        cfg = _make_cfg(tmp_path, with_db=True)
        assert _find_item_by_listing_id(cfg, "99999999") is None

    def test_returns_none_when_no_db(self, tmp_path):
        cfg = _make_cfg(tmp_path, with_db=False)
        assert _find_item_by_listing_id(cfg, "12345678") is None


# ---------------------------------------------------------------------------
# _log_offer_history
# ---------------------------------------------------------------------------

class TestLogOfferHistory:
    def test_writes_history_entry(self, tmp_path):
        cfg = _make_cfg(tmp_path, with_db=True)
        doc = {"sku": "tgw001", "ebay_listing": {"listing_id": "12345678"}}
        _insert_catalog(tmp_path / "catalog.db", "tgw001", doc)
        path = _write_item(cfg["itemdata_root"], "tgw001", doc)

        _log_offer_history(cfg, "12345678", "99001", "Accept", None, "claude", "2026-06-14T12:00:00Z")

        saved = json.loads(path.read_text())
        assert "offer_history" in saved
        assert len(saved["offer_history"]) == 1
        entry = saved["offer_history"][0]
        assert entry["offer_id"] == "99001"
        assert entry["action"] == "Accept"
        assert entry["by"] == "claude"

    def test_appends_multiple_entries(self, tmp_path):
        cfg = _make_cfg(tmp_path, with_db=True)
        doc = {"sku": "tgw001", "ebay_listing": {"listing_id": "12345678"}}
        _insert_catalog(tmp_path / "catalog.db", "tgw001", doc)
        _write_item(cfg["itemdata_root"], "tgw001", doc)

        _log_offer_history(cfg, "12345678", "99001", "Counter", 28.0, "claude", "2026-06-14T10:00:00Z")
        _log_offer_history(cfg, "12345678", "99001", "Accept", None, "dave", "2026-06-14T11:00:00Z")

        path = cfg["itemdata_root"] / "tgw001" / "tgw001.json"
        saved = json.loads(path.read_text())
        assert len(saved["offer_history"]) == 2
        assert saved["offer_history"][0]["action"] == "Counter"
        assert saved["offer_history"][0]["counter_price"] == 28.0
        assert saved["offer_history"][1]["action"] == "Accept"

    def test_counter_price_logged(self, tmp_path):
        cfg = _make_cfg(tmp_path, with_db=True)
        doc = {"sku": "tgw001", "ebay_listing": {"listing_id": "12345678"}}
        _insert_catalog(tmp_path / "catalog.db", "tgw001", doc)
        _write_item(cfg["itemdata_root"], "tgw001", doc)

        _log_offer_history(cfg, "12345678", "99001", "Counter", 27.50, "claude", "2026-06-14T12:00:00Z")

        path = cfg["itemdata_root"] / "tgw001" / "tgw001.json"
        saved = json.loads(path.read_text())
        assert saved["offer_history"][0]["counter_price"] == pytest.approx(27.50)

    def test_noop_when_listing_id_not_found(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        cfg = _make_cfg(tmp_path, with_db=True)
        _log_offer_history(cfg, "99999999", "99001", "Accept", None, "claude", "2026-06-14T12:00:00Z")
        # No exception — but this is no longer a silent drop: the C11
        # finding is persisted durably (see TestUnresolvedOfferRegistry).
        assert registry_path.exists()


# ---------------------------------------------------------------------------
# cmd_offers_list
# ---------------------------------------------------------------------------

class TestCmdOffersList:
    def _pending_response(self):
        offer_str = _offer_element_str(offer_id="99001", status="Pending",
                                       sku="tgw202601011200000", listing_id="12345678",
                                       offer_price="25.00", listing_price="35.00")
        return _make_get_best_offers_response([offer_str])

    def test_returns_offers(self):
        with patch("tgw.offers.get_best_offers", return_value=[{
            "offer_id": "99001", "sku": "tgw001", "status": "Pending",
            "offer_price": 25.0, "listing_price": 35.0, "listing_id": "12345678",
        }]):
            result = cmd_offers_list({"token": "x"})
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["offers"][0]["offer_id"] == "99001"

    def test_pending_only_filter(self):
        with patch("tgw.offers.get_best_offers", return_value=[]) as mock:
            cmd_offers_list({"token": "x"}, pending_only=True)
        mock.assert_called_once_with({"token": "x"}, status="Pending")

    def test_all_status_when_not_pending_only(self):
        with patch("tgw.offers.get_best_offers", return_value=[]) as mock:
            cmd_offers_list({"token": "x"}, pending_only=False)
        mock.assert_called_once_with({"token": "x"}, status="All")

    def test_sku_filter(self):
        offers = [
            {"offer_id": "1001", "sku": "tgw001", "status": "Pending", "listing_id": "111", "offer_price": 10.0, "listing_price": 15.0},
            {"offer_id": "1002", "sku": "tgw002", "status": "Pending", "listing_id": "222", "offer_price": 20.0, "listing_price": 30.0},
        ]
        with patch("tgw.offers.get_best_offers", return_value=offers):
            result = cmd_offers_list({"token": "x"}, sku="tgw001")
        assert result["count"] == 1
        assert result["offers"][0]["offer_id"] == "1001"

    def test_api_error_returns_ok_false(self):
        with patch("tgw.offers.get_best_offers", side_effect=RuntimeError("token expired")):
            result = cmd_offers_list({"token": "x"})
        assert result["ok"] is False
        assert "token expired" in result["error"]

    def test_auto_accept_eligible_offer(self):
        offers = [{
            "offer_id": "99001", "sku": "tgw001", "status": "Pending",
            "offer_price": 28.0, "listing_price": 35.0, "listing_id": "12345678",
        }]
        cfg = {"token": "x", "auto_accept_min_pct": 0.75}
        with patch("tgw.offers.get_best_offers", return_value=offers):
            with patch("tgw.offers.cmd_offers_respond", return_value={"ok": True}) as mock_respond:
                result = cmd_offers_list(cfg, auto_accept=True, dry_run=True)
        assert result["ok"] is True
        assert "99001" in result["auto_accepted"]
        mock_respond.assert_called_once()
        call_kwargs = mock_respond.call_args
        assert call_kwargs.kwargs["action"] == "Accept"

    def test_auto_accept_skips_below_threshold(self):
        offers = [{
            "offer_id": "99001", "sku": "tgw001", "status": "Pending",
            "offer_price": 20.0, "listing_price": 35.0, "listing_id": "12345678",
        }]
        cfg = {"token": "x", "auto_accept_min_pct": 0.80}  # 20/35 = 57% < 80%
        with patch("tgw.offers.get_best_offers", return_value=offers):
            with patch("tgw.offers.cmd_offers_respond") as mock_respond:
                cmd_offers_list(cfg, auto_accept=True)
        mock_respond.assert_not_called()

    def test_auto_accept_disabled_when_no_config(self):
        offers = [{
            "offer_id": "99001", "sku": "tgw001", "status": "Pending",
            "offer_price": 35.0, "listing_price": 35.0, "listing_id": "12345678",
        }]
        cfg = {"token": "x"}  # no auto_accept_min_pct
        with patch("tgw.offers.get_best_offers", return_value=offers):
            with patch("tgw.offers.cmd_offers_respond") as mock_respond:
                cmd_offers_list(cfg, auto_accept=True)
        mock_respond.assert_not_called()

    def test_auto_accept_skips_non_pending(self):
        offers = [{
            "offer_id": "99001", "sku": "tgw001", "status": "Accepted",
            "offer_price": 35.0, "listing_price": 35.0, "listing_id": "12345678",
        }]
        cfg = {"token": "x", "auto_accept_min_pct": 0.50}
        with patch("tgw.offers.get_best_offers", return_value=offers):
            with patch("tgw.offers.cmd_offers_respond") as mock_respond:
                cmd_offers_list(cfg, auto_accept=True)
        mock_respond.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_offers_respond
# ---------------------------------------------------------------------------

class TestCmdOffersRespond:
    def test_dry_run_returns_ok_without_api_call(self):
        with patch("tgw.offers.respond_to_best_offer") as mock_api:
            result = cmd_offers_respond(
                {"token": "x"}, "99001", "12345678", "Accept", dry_run=True
            )
        mock_api.assert_not_called()
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["action"] == "Accept"
        assert result["offer_id"] == "99001"
        assert result["listing_id"] == "12345678"
        assert "dry-run" in result["note"]

    def test_dry_run_counter_includes_price(self):
        result = cmd_offers_respond(
            {"token": "x"}, "99001", "12345678", "Counter", counter_price=28.0, dry_run=True
        )
        assert result["ok"] is True
        assert result["counter_price"] == pytest.approx(28.0)

    def test_dry_run_decline(self):
        result = cmd_offers_respond({"token": "x"}, "99001", "12345678", "Decline", dry_run=True)
        assert result["ok"] is True
        assert result["action"] == "Decline"

    def test_invalid_action_returns_error(self):
        result = cmd_offers_respond({"token": "x"}, "99001", "12345678", "Bargain", dry_run=True)
        assert result["ok"] is False
        assert "invalid action" in result["error"]

    def test_counter_without_price_returns_error(self):
        result = cmd_offers_respond(
            {"token": "x"}, "99001", "12345678", "Counter", counter_price=None, dry_run=True
        )
        assert result["ok"] is False
        assert "counter_price" in result["error"]

    def test_live_calls_api_and_logs(self, tmp_path):
        cfg = _make_cfg(tmp_path, with_db=True)
        doc = {"sku": "tgw001", "ebay_listing": {"listing_id": "12345678"}}
        _insert_catalog(tmp_path / "catalog.db", "tgw001", doc)
        _write_item(cfg["itemdata_root"], "tgw001", doc)

        with patch("tgw.offers.respond_to_best_offer") as mock_api:
            result = cmd_offers_respond(cfg, "99001", "12345678", "Accept", dry_run=False)

        mock_api.assert_called_once_with(
            cfg, offer_id="99001", listing_id="12345678", action="Accept", counter_price=None
        )
        assert result["ok"] is True
        assert result["dry_run"] is False
        # Verify offer_history was written
        path = cfg["itemdata_root"] / "tgw001" / "tgw001.json"
        saved = json.loads(path.read_text())
        assert len(saved["offer_history"]) == 1
        assert saved["offer_history"][0]["action"] == "Accept"

    def test_live_api_error_returns_ok_false(self):
        with patch("tgw.offers.respond_to_best_offer", side_effect=RuntimeError("api error")):
            result = cmd_offers_respond({"token": "x"}, "99001", "12345678", "Accept", dry_run=False)
        assert result["ok"] is False
        assert "api error" in result["error"]

    def test_by_field_included(self):
        result = cmd_offers_respond(
            {"token": "x"}, "99001", "12345678", "Accept", dry_run=True, by="dave"
        )
        assert result["by"] == "dave"

    def test_default_by_is_claude(self):
        result = cmd_offers_respond({"token": "x"}, "99001", "12345678", "Accept", dry_run=True)
        assert result["by"] == "claude"


# ---------------------------------------------------------------------------
# C11: unresolved-SKU Best-Offer finding (invariant C11, todo #1314)
# ---------------------------------------------------------------------------

class TestUnresolvedOfferRegistry:
    def test_live_success_with_unresolvable_sku_persists_finding(self, tmp_path, monkeypatch):
        """The core scenario: eBay API call SUCCEEDS (mocked), but SQLite
        catalog lookup misses -- the outcome must be durably recorded, not
        just logged and dropped."""
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        # SQLite catalog exists but has no row for this listing_id at all.
        cfg = _make_cfg(tmp_path, with_db=True)

        with patch("tgw.offers.respond_to_best_offer") as mock_api:
            result = cmd_offers_respond(
                cfg, "99001", "99999999", "Accept", dry_run=False, by="claude",
            )

        # eBay call happened and "succeeded" (mock didn't raise).
        mock_api.assert_called_once()
        assert result["ok"] is True
        assert result["dry_run"] is False

        # Durable finding was persisted -- queryable later, not just logged.
        assert registry_path.exists()
        registry = json.loads(registry_path.read_text())
        assert "99001" in registry
        entry = registry["99001"]
        assert entry["offer_id"] == "99001"
        assert entry["listing_id"] == "99999999"
        assert entry["action"] == "Accept"
        assert entry["by"] == "claude"
        assert entry["resolved"] is False
        assert entry["attempts"] == 1

    def test_record_unresolved_offer_writes_new_entry(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        _record_unresolved_offer("99002", "12345678", "Decline", None, "claude", "2026-07-13T10:00:00Z")

        registry = json.loads(registry_path.read_text())
        assert registry["99002"]["attempts"] == 1
        assert registry["99002"]["first_seen_at"] == "2026-07-13T10:00:00Z"
        assert registry["99002"]["resolved"] is False

    def test_record_unresolved_offer_bumps_attempts_on_repeat(self, tmp_path, monkeypatch):
        """Retry-friendliness: a second occurrence of the same offer_id
        (e.g. a future repair pass re-attempting resolution and failing
        again) increments attempts rather than clobbering history."""
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        _record_unresolved_offer("99003", "12345678", "Counter", 28.0, "claude", "2026-07-13T10:00:00Z")
        _record_unresolved_offer("99003", "12345678", "Counter", 28.0, "claude", "2026-07-13T11:00:00Z")

        registry = json.loads(registry_path.read_text())
        assert registry["99003"]["attempts"] == 2
        assert registry["99003"]["first_seen_at"] == "2026-07-13T10:00:00Z"
        assert registry["99003"]["last_attempt_at"] == "2026-07-13T11:00:00Z"

    def test_resolve_unresolved_offer_removes_entry(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        _record_unresolved_offer("99004", "12345678", "Accept", None, "claude", "2026-07-13T10:00:00Z")
        assert "99004" in json.loads(registry_path.read_text())

        _resolve_unresolved_offer("99004")
        assert "99004" not in json.loads(registry_path.read_text())

    def test_resolved_offer_found_by_listing_id_does_not_touch_registry(self, tmp_path, monkeypatch):
        """Sanity check: when SKU resolution succeeds, nothing is written
        to the unresolved registry at all."""
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        cfg = _make_cfg(tmp_path, with_db=True)
        doc = {"sku": "tgw001", "ebay_listing": {"listing_id": "12345678"}}
        _insert_catalog(tmp_path / "catalog.db", "tgw001", doc)
        _write_item(cfg["itemdata_root"], "tgw001", doc)

        with patch("tgw.offers.respond_to_best_offer"):
            cmd_offers_respond(cfg, "99005", "12345678", "Accept", dry_run=False)

        assert not registry_path.exists()


# ---------------------------------------------------------------------------
# repair_unresolved_offers (todo #1373, follow-up to #1314)
# ---------------------------------------------------------------------------

class TestRepairUnresolvedOffers:
    def test_repairs_entry_once_sku_now_resolvable(self, tmp_path, monkeypatch):
        """Core scenario: the registry has an unresolved entry recorded
        before the item existed locally; once the item shows up in the
        catalog, the repair pass must clear the registry entry and land
        the offer history on the found item."""
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        cfg = _make_cfg(tmp_path, with_db=True)

        # Seed the registry as if _record_unresolved_offer() had already run.
        offers_mod._record_unresolved_offer(
            "99010", "12345678", "Accept", None, "claude", "2026-07-13T10:00:00Z",
        )
        assert registry_path.exists()

        # SKU now resolvable: catalog row + item JSON now exist.
        doc = {"sku": "tgw001", "ebay_listing": {"listing_id": "12345678"}}
        _insert_catalog(tmp_path / "catalog.db", "tgw001", doc)
        item_path = _write_item(cfg["itemdata_root"], "tgw001", doc)

        result = offers_mod.repair_unresolved_offers(cfg)

        assert result["ok"] is True
        assert result["repaired"] == ["99010"]
        assert result["still_unresolved"] == []

        # Registry entry cleared.
        registry = json.loads(registry_path.read_text())
        assert "99010" not in registry

        # Offer history landed on the item.
        saved = json.loads(item_path.read_text())
        assert len(saved["offer_history"]) == 1
        entry = saved["offer_history"][0]
        assert entry["offer_id"] == "99010"
        assert entry["action"] == "Accept"
        assert entry["by"] == "claude"

    def test_still_unresolved_entry_bumps_attempts_and_stays(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        cfg = _make_cfg(tmp_path, with_db=True)
        offers_mod._record_unresolved_offer(
            "99011", "99999999", "Decline", None, "claude", "2026-07-13T10:00:00Z",
        )

        result = offers_mod.repair_unresolved_offers(cfg)

        assert result["ok"] is True
        assert result["repaired"] == []
        assert result["still_unresolved"] == ["99011"]

        registry = json.loads(registry_path.read_text())
        assert registry["99011"]["attempts"] == 2
        assert registry["99011"]["resolved"] is False

    def test_no_registry_file_is_a_noop(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)

        cfg = _make_cfg(tmp_path, with_db=True)
        result = offers_mod.repair_unresolved_offers(cfg)

        assert result == {"ok": True, "repaired": [], "still_unresolved": [], "total": 0}

    def test_already_resolved_entries_are_skipped(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "offers-unresolved.json"
        monkeypatch.setattr(offers_mod, "_UNRESOLVED_REGISTRY", registry_path)
        registry_path.write_text(json.dumps({
            "99012": {
                "offer_id": "99012", "listing_id": "12345678", "action": "Accept",
                "counter_price": None, "by": "claude",
                "first_seen_at": "2026-07-13T10:00:00Z", "last_attempt_at": "2026-07-13T10:00:00Z",
                "attempts": 1, "resolved": True,
            }
        }), encoding="utf-8")

        cfg = _make_cfg(tmp_path, with_db=True)
        result = offers_mod.repair_unresolved_offers(cfg)

        assert result["repaired"] == []
        assert result["still_unresolved"] == []
        assert result["total"] == 0
