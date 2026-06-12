"""PP-LOOKUP-001 / PP-REF-002 — tests for the product-lookup package.

Covers PriceCharting Tier 2, IGDB (video games), Discogs (music/records), and
dispatcher routing for all three. All HTTP is mocked; no network or API keys.
"""

import json

import requests.exceptions

import tgw.apis.lookup.discogs as discogs_mod
import tgw.apis.lookup.dispatcher as dispatcher
import tgw.apis.lookup.igdb as igdb_mod
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
    """Minimal HTTP response stub shared across all lookup tests."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

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


# ---------------------------------------------------------------------------
# PP-REF-002 — IGDB (video games)
# ---------------------------------------------------------------------------


def _igdb_creds(tmp_path):
    f = tmp_path / "igdb-credentials.json"
    f.write_text(json.dumps({"client_id": "cid", "client_secret": "csec"}))
    return {"secrets_root": str(tmp_path)}


def test_igdb_skips_without_credentials(tmp_path):
    assert igdb_mod.lookup("Mario", {"secrets_root": str(tmp_path)}) is None


def test_igdb_hit(tmp_path, monkeypatch):
    game_result = [{
        "id": 1, "name": "Super Mario 64",
        "genres": [{"name": "Platform"}],
        "platforms": [{"abbreviation": "N64"}],
        "first_release_date": 872121600,  # 1997-08-21
        "summary": "A classic 3D platformer.",
        "cover": {"url": "//images.igdb.com/t/cover_small/co1234.jpg"},
    }]
    post_calls = []

    def fake_post(url, **kwargs):
        post_calls.append(url)
        if "oauth2" in url:
            return _Resp({"access_token": "tok123", "expires_in": 3600})
        return _Resp(game_result)

    monkeypatch.setattr(igdb_mod.requests, "post", fake_post)
    # clear token cache so the token fetch fires
    igdb_mod._token_cache.clear()

    res = igdb_mod.lookup("Super Mario 64", _igdb_creds(tmp_path))
    assert res is not None
    assert res.source == "igdb"
    assert res.title == "Super Mario 64"
    assert res.category == "Platform"
    assert res.extra["platforms"] == "N64"
    assert res.extra["year"] == "1997"
    assert res.image_url.startswith("https://")
    assert len(post_calls) == 2  # token + game query


def test_igdb_uses_cached_token(tmp_path, monkeypatch):
    """Second call reuses the in-memory token without re-fetching."""
    game_result = [{"id": 2, "name": "Zelda", "summary": "Adventure game."}]
    post_calls = []

    def fake_post(url, **kwargs):
        post_calls.append(url)
        if "oauth2" in url:
            return _Resp({"access_token": "reused", "expires_in": 9999})
        return _Resp(game_result)

    monkeypatch.setattr(igdb_mod.requests, "post", fake_post)
    igdb_mod._token_cache.clear()

    igdb_mod.lookup("Zelda", _igdb_creds(tmp_path))  # seeds cache
    post_calls.clear()
    igdb_mod.lookup("Zelda 2", _igdb_creds(tmp_path))  # should skip token call
    assert all("oauth2" not in url for url in post_calls)


def test_igdb_miss_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(igdb_mod, "_get_token", lambda *a: "tok")
    monkeypatch.setattr(igdb_mod.requests, "post", lambda *a, **k: _Resp([]))
    assert igdb_mod.lookup("Unknown Game XYZ", _igdb_creds(tmp_path)) is None


def test_igdb_request_error_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(igdb_mod, "_get_token", lambda *a: "tok")
    monkeypatch.setattr(igdb_mod.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(
                            requests.exceptions.RequestException("timeout")))
    assert igdb_mod.lookup("Mario", _igdb_creds(tmp_path)) is None


def test_igdb_token_failure_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(igdb_mod, "_get_token", lambda *a: None)
    assert igdb_mod.lookup("Mario", _igdb_creds(tmp_path)) is None


# ---------------------------------------------------------------------------
# PP-REF-002 — Discogs (music/records)
# ---------------------------------------------------------------------------


def _discogs_creds(tmp_path):
    f = tmp_path / "discogs-credentials.json"
    f.write_text(json.dumps({"personal_access_token": "tok_discogs"}))
    return {"secrets_root": str(tmp_path)}


def test_discogs_skips_without_credentials(tmp_path):
    assert discogs_mod.lookup("012345678901", {"secrets_root": str(tmp_path)}) is None


def test_discogs_hit(tmp_path, monkeypatch):
    hit = {
        "title": "Dark Side of the Moon",
        "year": 1973,
        "label": ["Harvest", "EMI"],
        "genre": ["Rock"],
        "format": ["Vinyl", "LP"],
        "cover_image": "https://img.discogs.com/cover.jpg",
    }
    monkeypatch.setattr(discogs_mod.requests, "get",
                        lambda *a, **k: _Resp({"results": [hit]}))

    res = discogs_mod.lookup("012345678901", _discogs_creds(tmp_path))
    assert res is not None
    assert res.source == "discogs"
    assert res.title == "Dark Side of the Moon"
    assert res.brand == "Harvest, EMI"
    assert res.category == "Rock"
    assert res.upc == "012345678901"
    assert res.image_url == "https://img.discogs.com/cover.jpg"
    assert "1973" in res.description


def test_discogs_miss_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(discogs_mod.requests, "get",
                        lambda *a, **k: _Resp({"results": []}))
    assert discogs_mod.lookup("000000000000", _discogs_creds(tmp_path)) is None


def test_discogs_request_error_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(discogs_mod.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            requests.exceptions.RequestException("timeout")))
    assert discogs_mod.lookup("012345678901", _discogs_creds(tmp_path)) is None


# ---------------------------------------------------------------------------
# Dispatcher routing — music and game paths
# ---------------------------------------------------------------------------

def test_dispatcher_routes_to_discogs_for_music_barcode(monkeypatch):
    """Music keyword + UPC → Discogs tried first."""
    item = {"upc": "012345678901", "ai_hint": "vinyl record lp"}
    hit = LookupResult(source="discogs", fetched_at="2026-06-12T00:00:00+00:00",
                       title="Dark Side")
    monkeypatch.setattr(dispatcher.discogs, "lookup", lambda *a: hit)
    res = dispatcher.lookup_product(item, {})
    assert res.source == "discogs"


def test_dispatcher_discogs_miss_falls_back_to_upcitemdb(monkeypatch):
    """Discogs miss on music item → upcitemdb fallback."""
    item = {"upc": "012345678901", "ai_hint": "music vinyl"}
    upc_hit = LookupResult(source="upcitemdb", fetched_at="2026-06-12T00:00:00+00:00",
                           title="Some Album")
    monkeypatch.setattr(dispatcher.discogs, "lookup", lambda *a: None)
    monkeypatch.setattr(dispatcher.upcitemdb, "lookup", lambda *a: upc_hit)
    monkeypatch.setattr(dispatcher.go_upc, "lookup", lambda *a: None)
    res = dispatcher.lookup_product(item, {})
    assert res.source == "upcitemdb"


def test_dispatcher_routes_to_igdb_for_game_no_barcode(monkeypatch):
    """Game keyword + no barcode → IGDB tried."""
    item = {"ai_hint": "Nintendo 64 video game", "title": "Super Mario 64"}
    igdb_hit = LookupResult(source="igdb", fetched_at="2026-06-12T00:00:00+00:00",
                            title="Super Mario 64")
    monkeypatch.setattr(dispatcher.igdb, "lookup", lambda *a: igdb_hit)
    monkeypatch.setattr(dispatcher.pricecharting, "lookup",
                        lambda *a: (_ for _ in ()).throw(AssertionError("should not reach pc")))
    res = dispatcher.lookup_product(item, {})
    assert res.source == "igdb"
