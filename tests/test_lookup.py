"""PP-LOOKUP-001 — first tests for the product-lookup package.

Covers the new PriceCharting Tier 2 module (graceful-skip, penny->dollar, hit/
miss) and the dispatcher routing/cache the ai_identify hot path depends on. All
HTTP is mocked; no network or API keys are used.
"""

import json

import tgw.apis.lookup.dispatcher as dispatcher
import tgw.apis.lookup.pricecharting as pc
from tgw.apis.lookup.base import LookupResult, barcode_from_item

# ---------------------------------------------------------------------------
# pricecharting helpers
# ---------------------------------------------------------------------------

def test_to_dollars_conversion():
    assert pc._to_dollars(1999) == 19.99
    assert pc._to_dollars("500") == 5.0
    assert pc._to_dollars(0) is None       # non-positive dropped
    assert pc._to_dollars(-5) is None
    assert pc._to_dollars(None) is None
    assert pc._to_dollars("nope") is None


def test_pricecharting_skips_without_token(tmp_path):
    # No credentials file -> inert (returns None), like IGDB.
    cfg = {"secrets_root": str(tmp_path)}
    assert pc.lookup("Super Mario 64", cfg) is None


def _token_cfg(tmp_path):
    (tmp_path / "pricecharting-credentials.json").write_text(
        json.dumps({"token": "TESTTOKEN"}), encoding="utf-8")
    return {"secrets_root": str(tmp_path)}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_pricecharting_hit(tmp_path, monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _Resp({
            "id": "6910",
            "product-name": "Super Mario 64",
            "console-name": "Nintendo 64",
            "loose-price": 3500,
            "cib-price": 8000,
            "new-price": 25000,
        })

    monkeypatch.setattr(pc.requests, "get", fake_get)
    res = pc.lookup("Super Mario 64", _token_cfg(tmp_path))
    assert isinstance(res, LookupResult)
    assert res.source == "pricecharting"
    assert res.title == "Super Mario 64"
    assert res.category == "Nintendo 64"
    assert res.msrp == 250.0                  # new-price preferred
    assert res.extra["loose_price"] == 35.0
    assert res.extra["cib_price"] == 80.0
    assert captured["params"]["t"] == "TESTTOKEN"


def test_pricecharting_miss_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pc.requests, "get",
                        lambda *a, **k: _Resp({"status": "error"}))
    assert pc.lookup("Nonexistent Game", _token_cfg(tmp_path)) is None


def test_pricecharting_msrp_falls_back_to_loose(tmp_path, monkeypatch):
    monkeypatch.setattr(pc.requests, "get", lambda *a, **k: _Resp({
        "id": "1", "product-name": "Loose Only", "loose-price": 1200,
    }))
    res = pc.lookup("Loose Only", _token_cfg(tmp_path))
    assert res.msrp == 12.0


# ---------------------------------------------------------------------------
# dispatcher routing
# ---------------------------------------------------------------------------

def test_barcode_from_item_skips_does_not_apply():
    assert barcode_from_item({"upc": "Does not apply"}) == ("", "")
    assert barcode_from_item({"upc": "012345678905"}) == ("012345678905", "upc")


def test_dispatcher_routes_to_pricecharting_when_others_miss(monkeypatch):
    # A game item with no barcode; IGDB misses -> PriceCharting fills it.
    item = {"ai_hint": "Nintendo 64 video game", "title": "Super Mario 64"}
    monkeypatch.setattr(dispatcher.igdb, "lookup", lambda title, cfg: None)
    sentinel = LookupResult(source="pricecharting", fetched_at="2026-06-07T00:00:00+00:00",
                            title="Super Mario 64", msrp=250.0)
    called = {}

    def fake_pc(title, cfg):
        called["t"] = title
        return sentinel

    monkeypatch.setattr(dispatcher.pricecharting, "lookup", fake_pc)

    res = dispatcher.lookup_product(item, {})
    assert res is sentinel
    # _search_title prefers ai_hint over title for the name-based query.
    assert called["t"] == "Nintendo 64 video game"


def test_dispatcher_pricecharting_not_called_when_igdb_hits(monkeypatch):
    # Strictly additive: if IGDB already resolved, PriceCharting must not fire.
    item = {"ai_hint": "Nintendo 64 video game", "title": "Super Mario 64"}
    igdb_hit = LookupResult(source="igdb", fetched_at="2026-06-07T00:00:00+00:00",
                            title="Super Mario 64")
    monkeypatch.setattr(dispatcher.igdb, "lookup", lambda title, cfg: igdb_hit)

    def _boom(title, cfg):
        raise AssertionError("pricecharting should not be called when IGDB hit")

    monkeypatch.setattr(dispatcher.pricecharting, "lookup", _boom)
    res = dispatcher.lookup_product(item, {})
    assert res is igdb_hit


def test_dispatcher_returns_fresh_cache_without_calling_sources(monkeypatch):
    from tgw.apis.lookup.base import now_iso
    item = {
        "ai_hint": "Nintendo 64 video game",
        "title": "Super Mario 64",
        "product_lookup": {"source": "cache", "fetched_at": now_iso(),
                           "title": "Cached Title"},
    }

    def _boom(*a, **k):
        raise AssertionError("no source should be called when cache is fresh")

    monkeypatch.setattr(dispatcher.igdb, "lookup", _boom)
    monkeypatch.setattr(dispatcher.pricecharting, "lookup", _boom)
    res = dispatcher.lookup_product(item, {})
    assert res.title == "Cached Title"
    assert res.source == "cache"
