"""Environment isolation for operator item-page eBay reference data."""

from __future__ import annotations

import json

from tgw import http_server
from tgw.apis.ebay import trading
from tgw.ebay import sync


def _cfg(root, environment):
    return {
        "catalog_root": root,
        "ebay_environment": environment,
    }


def test_item_reference_snapshot_names_preserve_prod_and_namespace_sandbox(
    tmp_path,
):
    production = _cfg(tmp_path, "production")
    sandbox = _cfg(tmp_path, "sandbox")

    assert http_server._ebay_reference_snapshot_path(
        production,
        "ebay-store-categories.json",
    ).name == "ebay-store-categories.json"
    assert http_server._ebay_reference_snapshot_path(
        sandbox,
        "ebay-store-categories.json",
    ).name == "ebay-sandbox-store-categories.json"
    assert http_server._ebay_reference_snapshot_path(
        production,
        "ebay-fulfillment-policies.json",
    ).name == "ebay-fulfillment-policies.json"
    assert http_server._ebay_reference_snapshot_path(
        sandbox,
        "ebay-fulfillment-policies.json",
    ).name == "ebay-sandbox-fulfillment-policies.json"

    (tmp_path / "ebay-store-categories.json").write_text(json.dumps({
        "results": [{"id": "PROD-STORE", "name": "Production Store"}],
    }))
    (tmp_path / "ebay-sandbox-store-categories.json").write_text(json.dumps({
        "results": [{"id": "SBX-STORE", "name": "Sandbox Store"}],
    }))
    (tmp_path / "ebay-fulfillment-policies.json").write_text(json.dumps({
        "fulfillment": {"PROD-SHIP": "Production Shipping"},
        "return": {"PROD-RETURN": "Production Returns"},
    }))
    (tmp_path / "ebay-sandbox-fulfillment-policies.json").write_text(json.dumps({
        "fulfillment": {"SBX-SHIP": "Sandbox Shipping"},
        "return": {"SBX-RETURN": "Sandbox Returns"},
    }))

    assert http_server._store_categories_snapshot(production)[0][0]["id"] == "PROD-STORE"
    assert http_server._store_categories_snapshot(sandbox)[0][0]["id"] == "SBX-STORE"
    assert http_server._fulfillment_policies_snapshot(production)[0] == {
        "PROD-SHIP": "Production Shipping",
    }
    assert http_server._fulfillment_policies_snapshot(sandbox)[0] == {
        "SBX-SHIP": "Sandbox Shipping",
    }
    assert http_server._return_policies_snapshot(production)[0] == {
        "PROD-RETURN": "Production Returns",
    }
    assert http_server._return_policies_snapshot(sandbox)[0] == {
        "SBX-RETURN": "Sandbox Returns",
    }


def test_item_reference_process_caches_are_isolated_by_environment(
    tmp_path,
    monkeypatch,
):
    production = _cfg(tmp_path, "production")
    sandbox = _cfg(tmp_path, "sandbox")
    calls = {"store": [], "fulfillment": [], "return": []}

    def environment(cfg):
        return cfg["ebay_environment"]

    monkeypatch.setattr(
        trading,
        "get_store_categories",
        lambda cfg: calls["store"].append(environment(cfg)) or [{
            "id": f"{environment(cfg)}-store",
            "name": f"{environment(cfg)} store",
        }],
    )
    monkeypatch.setattr(
        sync,
        "get_fulfillment_policies_full",
        lambda cfg: calls["fulfillment"].append(environment(cfg)) or [{
            "id": f"{environment(cfg)}-ship",
            "name": f"{environment(cfg)} shipping",
        }],
    )
    monkeypatch.setattr(
        sync,
        "get_return_policies_full",
        lambda cfg: calls["return"].append(environment(cfg)) or [{
            "id": f"{environment(cfg)}-return",
            "name": f"{environment(cfg)} returns",
        }],
    )
    http_server._LIVE_STORE_CATS_CACHE.clear()
    http_server._LIVE_FULFILLMENT_POLICIES_CACHE.clear()
    http_server._LIVE_RETURN_POLICIES_CACHE.clear()

    for cfg in (production, sandbox, production, sandbox):
        expected = environment(cfg)
        assert http_server._live_store_categories(cfg)[0][0]["id"] == f"{expected}-store"
        assert http_server._live_fulfillment_policies(cfg)[0] == {
            f"{expected}-ship": f"{expected} shipping",
        }
        assert http_server._live_return_policies(cfg)[0] == {
            f"{expected}-return": f"{expected} returns",
        }

    assert calls == {
        "store": ["production", "sandbox"],
        "fulfillment": ["production", "sandbox"],
        "return": ["production", "sandbox"],
    }
    assert set(http_server._LIVE_STORE_CATS_CACHE) == {"production", "sandbox"}
    assert set(http_server._LIVE_FULFILLMENT_POLICIES_CACHE) == {
        "production",
        "sandbox",
    }
    assert set(http_server._LIVE_RETURN_POLICIES_CACHE) == {
        "production",
        "sandbox",
    }

    for cache in (
        http_server._LIVE_STORE_CATS_CACHE,
        http_server._LIVE_FULFILLMENT_POLICIES_CACHE,
        http_server._LIVE_RETURN_POLICIES_CACHE,
    ):
        http_server._clear_ebay_reference_cache(cache, production)
        assert set(cache) == {"sandbox"}
